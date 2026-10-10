/** Data & Exposure → Producers & offers, the offers and type-subscription boxes: producer data offers (`data.offers`, server-paged with a type
 * filter, "notify data ready", terminate, and the admin "create a producer offer" form; DME commits one of the offered methods, section 3.5) and
 * the type subscriptions (`data.type-subscriptions`, server-paged, subscribe / unsubscribe). */
import { useState } from "react";

import type { DataOffer, DmeTypeSubscription } from "../../../api/types";
import { ActionButton, Can, Card, Field, Id } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { PATHS, useDmeTypes } from "../data/queries";
import { DELIVERY_METHODS } from "./DataJobs";

/** The offers box. */
export function Offers() {
  const types = useDmeTypes();
  const typeName = (id: string) => types.data?.find((t) => t.dmeTypeId === id)?.typeName ?? id.slice(0, 8);
  const [typeFilter, setTypeFilter] = useState("");
  const [typeId, setTypeId] = useState("");
  const [methods, setMethods] = useState<string[]>(["PUSH_HTTP", "PULL_HTTP"]);
  const [terminateUri, setTerminateUri] = useState("http://producer:8000/offer-terminated");
  return (
    <Card section="data.offers" title="Data offers (producers)" sub="DME commits one of the offered methods (section 3.5)"
      actions={<select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} aria-label="Filter offers by type"><option value="">Any type</option>{types.data?.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}</select>}>
      <ServerTable<DataOffer> path={PATHS.offers} query={{ dme_type_id: typeFilter || undefined }} rowKey={(o) => o.offerId} empty="No data offers." columns={[
        { header: "Offer", render: (o) => <Id value={o.offerId} /> }, { header: "Type", render: (o) => <code>{typeName(o.dmeTypeId)}</code> },
        { header: "Offered", render: (o) => o.dataDeliveryMethodsOffered.join(", ") },
        { header: "Committed", render: (o) => o.committedMethod ? <span className="badge b-ok">{o.committedMethod}</span> : <span className="muted">—</span> },
        { header: "", className: "actions", render: (o) => <div className="row gap end">
          <ActionButton label="Notify data ready" title="The producer's reversed-direction availability signal" action={{ method: "POST", path: `${PATHS.offers}/${o.offerId}/notify`, json: { availableAt: new Date().toISOString() }, success: "Availability notified" }} />
          <ActionButton label="Terminate" tone="danger" confirm="Terminate this offer?" action={{ method: "DELETE", path: `${PATHS.offers}/${o.offerId}`, success: "Offer terminated" }} />
        </div> },
      ]} />
      <Can method="POST" path={PATHS.offers}>
        <details className="admin-tools">
          <summary>Admin: create a producer offer</summary>
          <div className="form inline">
            <Field label="Type"><select value={typeId} onChange={(e) => setTypeId(e.target.value)}><option value="">Choose…</option>{types.data?.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}</select></Field>
            <Field label="Methods offered" hint="Ctrl/Cmd-click for several"><select multiple size={3} value={methods} onChange={(e) => setMethods([...e.target.selectedOptions].map((o) => o.value))}>{DELIVERY_METHODS.map((m) => <option key={m}>{m}</option>)}</select></Field>
            <Field label="Termination notification URI"><input value={terminateUri} onChange={(e) => setTerminateUri(e.target.value)} /></Field>
          </div>
          <ActionButton label="Create offer" disabled={!typeId || methods.length === 0} action={{
            method: "POST", path: PATHS.offers, success: "Offer created",
            json: { dmeTypeId: typeId, dataDeliveryMode: "CONTINUOUS", dataDeliveryMethods: methods, dataOfferTerminationNotificationUri: terminateUri },
          }} />
        </details>
      </Can>
    </Card>
  );
}

/** The type subscriptions box. */
export function TypeSubscriptions() {
  const [dest, setDest] = useState("");
  const [owner, setOwner] = useState("smo-gui");
  return (
    <Card section="data.type-subscriptions" title="Type subscriptions" sub="Notified whenever any type is registered or removed">
      <Can method="POST" path={PATHS.typeSubscriptions}>
        <div className="form inline">
          <Field label="Notification destination"><input value={dest} onChange={(e) => setDest(e.target.value)} placeholder="http://consumer:8000/dme-types" /></Field>
          <Field label="Owner"><input value={owner} onChange={(e) => setOwner(e.target.value)} /></Field>
          <ActionButton label="Subscribe" disabled={!dest} action={{ method: "POST", path: PATHS.typeSubscriptions, json: { notificationDestination: dest, owner }, success: "Subscribed to type changes" }} />
        </div>
      </Can>
      <ServerTable<DmeTypeSubscription> path={PATHS.typeSubscriptions} rowKey={(s) => s.subscriptionId} empty="No type subscriptions." columns={[
        { header: "Subscription", render: (s) => <Id value={s.subscriptionId} /> }, { header: "Owner", render: (s) => s.owner },
        { header: "Destination", render: (s) => <code className="small">{s.notificationDestination}</code> },
        { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `${PATHS.typeSubscriptions}/${s.subscriptionId}`, success: "Unsubscribed" }} /> },
      ]} />
    </Card>
  );
}
