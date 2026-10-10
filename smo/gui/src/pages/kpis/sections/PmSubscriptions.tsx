/** Section `kpis.pm` (PM tab): RAN NF OAM PM subscriptions as a server-paged table with Unsubscribe, and the role-gated "New PM subscription"
 * form (SubscribePM registers a DME producer type for the counter, RAN NF OAM LLD 3.5; the counters themselves are consumed through DME). */
import { useState } from "react";

import type { PmSubscription } from "../../../api/types";
import { ActionButton, Can, Card, Field, Id } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { PM_SUBSCRIPTIONS, useO1Endpoints } from "../data/queries";

/** The tab. */
export function PmSubscriptions() {
  return (
    <>
      <Can method="POST" path={PM_SUBSCRIPTIONS}><NewPmSubscription /></Can>
      <Card section="kpis.pm" title="PM subscriptions">
        <ServerTable<PmSubscription> path={PM_SUBSCRIPTIONS} rowKey={(s) => s.subscriptionId} empty="No PM subscriptions." columns={[
          { header: "Subscription", render: (s) => <Id value={s.subscriptionId} /> },
          { header: "Managed element", render: (s) => s.managedElementRef },
          { header: "Counter", render: (s) => <code>{s.counterType}</code> },
          { header: "Delivery", render: (s) => s.deliveryMethod },
          { header: "Southbound engine", render: (s) => s.southboundEngine },
          { header: "Granularity", render: (s) => (s.granularityPeriod ? `${s.granularityPeriod} s` : "—") },
          { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `${PM_SUBSCRIPTIONS}/${s.subscriptionId}`, success: "Unsubscribed" }} /> },
        ]} />
      </Card>
    </>
  );
}

/** The form: element, counter, delivery method and granularity, sent as query parameters as the route takes them. */
function NewPmSubscription() {
  const endpoints = useO1Endpoints();
  const [f, setF] = useState({ managed_element_ref: "", counter_type: "DRB.UEThpDl", delivery_method: "push", granularity_period: "900" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <Card section="kpis.newPm" title="New PM subscription">
      <p className="muted small">SubscribePM registers a DME producer type for the counter (RAN NF OAM LLD 3.5) — the counters themselves are consumed through DME. Live file collection is out of scope.</p>
      <div className="form inline">
        <Field label="Managed element"><select value={f.managed_element_ref} onChange={set("managed_element_ref")}><option value="">Choose…</option>{endpoints.data?.map((e) => <option key={e.endpointId}>{e.managedElementRef}</option>)}</select></Field>
        <Field label="Counter type"><input value={f.counter_type} onChange={set("counter_type")} /></Field>
        <Field label="Delivery"><select value={f.delivery_method} onChange={set("delivery_method")}><option value="pull">pull (ProvMnS)</option><option value="push">push (PMJobControl)</option><option value="stream">stream (StreamingDataReporting)</option><option value="file">file (FileDataReporting)</option></select></Field>
        <Field label="Granularity (s)"><input type="number" min={1} value={f.granularity_period} onChange={set("granularity_period")} /></Field>
        <ActionButton label="Subscribe" tone="primary" disabled={!f.managed_element_ref} action={{ method: "POST", path: PM_SUBSCRIPTIONS, query: f, success: "PM subscription created" }} />
      </div>
    </Card>
  );
}
