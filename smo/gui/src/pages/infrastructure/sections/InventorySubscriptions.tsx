/** Infrastructure → O-Cloud inventory, the inventory-change subscriptions box (`infrastructure.inventory-subscriptions`): who FOCOM notifies on
 * provision / deprovision (CREATE / DELETE), a role-gated subscribe form, and unsubscribe. Server-paged. */
import { useState } from "react";

import type { InventorySubscription } from "../../../api/types";
import { ActionButton, Can, Card, Field, Id } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { INVENTORY_POLL, PATHS } from "../data/queries";

/** The box. */
export function InventorySubscriptions() {
  const [callback, setCallback] = useState("");
  const [typeId, setTypeId] = useState("");
  return (
    <Card section="infrastructure.inventory-subscriptions" title="Inventory-change subscriptions" sub="Notified on provision / deprovision (CREATE / DELETE)">
      <Can method="POST" path={PATHS.inventorySubscriptions}>
        <div className="form inline">
          <Field label="Callback"><input value={callback} onChange={(e) => setCallback(e.target.value)} placeholder="http://consumer:8000/inventory-events" /></Field>
          <Field label="Resource type filter"><input value={typeId} onChange={(e) => setTypeId(e.target.value)} placeholder="any" /></Field>
          <ActionButton label="Subscribe" disabled={!callback} action={{ method: "POST", path: PATHS.inventorySubscriptions, json: { callback, resourceTypeId: typeId || null, consumerSubscriptionId: "smo-gui" }, success: "Subscribed to inventory changes" }} />
        </div>
      </Can>
      <ServerTable<InventorySubscription> path={PATHS.inventorySubscriptions} rowKey={(s) => s.subscriptionId} empty="No inventory subscriptions." refetchInterval={INVENTORY_POLL} columns={[
        { header: "Subscription", render: (s) => <Id value={s.subscriptionId} /> }, { header: "Callback", render: (s) => <code className="small">{s.callback}</code> },
        { header: "Resource type", render: (s) => s.resourceTypeId ?? "any" },
        { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `${PATHS.inventorySubscriptions}/${s.subscriptionId}`, success: "Unsubscribed" }} /> },
      ]} />
    </Card>
  );
}
