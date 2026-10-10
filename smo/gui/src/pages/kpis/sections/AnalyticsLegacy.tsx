/** Sections `kpis.analyticsReports`, `kpis.producers` and `kpis.analyticsSubs` (RAN Analytics tab): the pre-TS 28.104 MDAF analytics, kept as
 * they were. Analytics reports as a server-paged table filtered by analytics type on the server (a click shows the output); the RAN Analytics
 * producers; and the poll-based analytics subscriptions with a role-gated Subscribe and Unsubscribe. */
import { useState } from "react";

import type { AnalyticsProducer, AnalyticsReport, AnalyticsSubscription } from "../../../api/types";
import { ActionButton, Can, Card, Field, Id, Json, Modal } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { MDAF_REPORTS, MDAF_SUBSCRIPTIONS, PRODUCERS, useProducers } from "../data/queries";

/** The analytics types the registered producers offer (the type pickers). */
export function useAnalyticsTypes(): string[] {
  const producers = useProducers();
  return [...new Set((producers.data ?? []).map((p) => p.analyticsType))];
}

/** Analytics reports. */
export function AnalyticsReports() {
  const types = useAnalyticsTypes();
  const [type, setType] = useState("");
  const [shown, setShown] = useState<AnalyticsReport | null>(null);
  return (
    <Card section="kpis.analyticsReports" title="Analytics reports (MDAF)" actions={<select value={type} onChange={(e) => setType(e.target.value)} aria-label="Analytics type"><option value="">All types</option>{types.map((t) => <option key={t}>{t}</option>)}</select>}>
      <ServerTable<AnalyticsReport> path={MDAF_REPORTS} query={{ analytics_type: type || undefined }} rowKey={(r) => r.reportId} empty="No analytics reports published." onRowClick={setShown} columns={[
        { header: "Report", render: (r) => <Id value={r.reportId} /> },
        { header: "Type", render: (r) => r.analyticsType },
        { header: "Output", render: (r) => <code className="small clip">{JSON.stringify(r.output)}</code> },
      ]} />
      {shown && <Modal title={`${shown.analyticsType} report`} onClose={() => setShown(null)}><Json value={shown.output} /></Modal>}
    </Card>
  );
}

/** RAN Analytics producers. */
export function Producers() {
  return (
    <Card section="kpis.producers" title="Producers">
      <ServerTable<AnalyticsProducer> path={PRODUCERS} rowKey={(p) => `${p.producerId}/${p.analyticsType}`} empty="No producers registered." columns={[
        { header: "Producer", render: (p) => p.producerId }, { header: "Type", render: (p) => p.analyticsType },
        { header: "MDAType (TS28104)", render: (p) => p.mdaType ?? <span className="muted">—</span> },
        { header: "DME inputs", render: (p) => p.dmeInputTypes.length },
      ]} />
    </Card>
  );
}

/** Analytics subscriptions, with Subscribe (role-gated). */
export function AnalyticsSubscriptions() {
  const types = useAnalyticsTypes();
  const [newType, setNewType] = useState("");
  return (
    <Card section="kpis.analyticsSubs" title="Subscriptions">
      <Can method="POST" path={MDAF_SUBSCRIPTIONS}>
        <div className="form inline">
          <Field label="Analytics type"><input list="an-types" value={newType} onChange={(e) => setNewType(e.target.value)} /><datalist id="an-types">{types.map((t) => <option key={t} value={t} />)}</datalist></Field>
          <ActionButton label="Subscribe" disabled={!newType} action={{ method: "POST", path: MDAF_SUBSCRIPTIONS, query: { analytics_type: newType, requested_by: "smo-gui" }, success: "Subscribed (poll-based)" }} />
        </div>
      </Can>
      <ServerTable<AnalyticsSubscription> path={MDAF_SUBSCRIPTIONS} rowKey={(s) => s.subscriptionId} empty="No subscriptions." columns={[
        { header: "Type", render: (s) => s.analyticsType }, { header: "Requested by", render: (s) => s.requestedBy },
        { header: "Delivery", render: (s) => s.notificationDestination ?? <span className="muted">poll</span> },
        { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `${MDAF_SUBSCRIPTIONS}/${s.subscriptionId}`, success: "Unsubscribed" }} /> },
      ]} />
    </Card>
  );
}
