/** Section `alarms.fm` (tab "FM subscriptions", HISTORY.md OI-6.7): SubscribeFM registers RAN NF OAM as a DME producer of RAN.FaultRecords for one
 * managed element; the list of subscriptions is a server table with Unsubscribe. Both actions are RBAC-gated (`Can`, `ActionButton`). */
import { useState } from "react";

import type { FmSubscription } from "../../../api/types";
import { ActionButton, Can, Card, Field, Id } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { FM_SUBSCRIPTIONS, useO1Endpoints } from "../data/queries";

/** The new-subscription form and the subscription list. */
export function FmSubscriptions() {
  const endpoints = useO1Endpoints();
  const [f, setF] = useState({ managed_element_ref: "", delivery_method: "push" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="stack" data-section="alarms.fm">
      <Can method="POST" path={FM_SUBSCRIPTIONS}>
        <Card title="New FM subscription">
          <p className="muted small">SubscribeFM registers RAN NF OAM as a DME producer for RAN.FaultRecords (mirrors SubscribePM) — gives an rApp/AI-ML model DME-mediated visibility into outstanding/historical alarms. It never clears an alarm; that stays the Ack/Clear actions on the RAN tab.</p>
          <div className="form inline">
            <Field label="Managed element"><select value={f.managed_element_ref} onChange={set("managed_element_ref")}><option value="">Choose…</option>{endpoints.data?.map((e) => <option key={e.endpointId}>{e.managedElementRef}</option>)}</select></Field>
            <Field label="Delivery"><select value={f.delivery_method} onChange={set("delivery_method")}><option value="pull">pull</option><option value="push">push</option><option value="stream">stream</option></select></Field>
            <ActionButton label="Subscribe" tone="primary" disabled={!f.managed_element_ref} action={{ method: "POST", path: FM_SUBSCRIPTIONS, query: f, success: "FM subscription created" }} />
          </div>
        </Card>
      </Can>
      <Card title="FM subscriptions">
        <ServerTable<FmSubscription> path={FM_SUBSCRIPTIONS} rowKey={(s) => s.subscriptionId} empty="No FM subscriptions." columns={[
          { header: "Subscription", render: (s) => <Id value={s.subscriptionId} /> },
          { header: "Managed element", render: (s) => s.managedElementRef },
          { header: "Delivery", render: (s) => s.deliveryMethod },
          { header: "Southbound engine", render: (s) => s.southboundEngine },
          { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `${FM_SUBSCRIPTIONS}/${s.subscriptionId}`, success: "Unsubscribed" }} /> },
        ]} />
      </Card>
    </div>
  );
}
