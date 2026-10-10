/** Data & Exposure → SME, the discovery and CAPIF event boxes: service discovery as one invoker sees it (`data.discovery`; its invoker list is read when the picker is first used; the route's own
 * `?api_name=` search, allowedConsumers hides services from invokers not listed) and the CAPIF event subscriptions of one subscriber
 * (`data.capif-subscriptions`, server-paged, with event-type and service / AEF / invoker filters). Moved from the pre-redesign page. */
import { useState } from "react";

import type { CapifEventSubscription } from "../../../api/types";
import { ActionButton, Can, Card, DataTable, Field, Id } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { splitList } from "../../../lib/domain";
import { PATHS, useDiscovery, useInvokerChoices } from "../data/queries";

const EVENT_TYPES = ["SERVICE_API_AVAILABLE", "SERVICE_API_UNAVAILABLE", "SERVICE_API_UPDATE",
  "API_INVOKER_ONBOARDED", "API_INVOKER_UPDATED", "API_INVOKER_OFFBOARDED"];

/** The discovery box. */
export function Discovery() {
  const [load, setLoad] = useState(false);
  const invokers = useInvokerChoices(load);
  const [invoker, setInvoker] = useState("");
  const [apiName, setApiName] = useState("");
  const found = useDiscovery(invoker, apiName);
  return (
    <Card section="data.discovery" title="Service discovery (as an invoker sees it)" sub="allowedConsumers hides services from invokers not listed">
      <div className="form inline">
        <Field label="Discover as invoker"><select value={invoker} onFocus={() => setLoad(true)} onPointerDown={() => setLoad(true)} onChange={(e) => setInvoker(e.target.value)}><option value="">Choose…</option>{invokers.data?.items.map((i) => <option key={i.apiInvokerId} value={i.apiInvokerId}>{i.apiInvokerId}</option>)}</select></Field>
        <Field label="API name filter"><input value={apiName} onChange={(e) => setApiName(e.target.value)} /></Field>
      </div>
      {invoker && <DataTable rows={found.data} loading={found.isLoading} error={found.error} rowKey={(s) => s.serviceId} empty="No services visible to this invoker." columns={[
        { header: "Service", render: (s) => <strong>{s.serviceName}</strong> }, { header: "Producer", render: (s) => s.producerId },
        { header: "Version", render: (s) => s.version }, { header: "Endpoint", render: (s) => <code className="small">{s.endpoint}</code> },
      ]} />}
    </Card>
  );
}

/** The CAPIF event subscriptions box. */
export function EventSubscriptions() {
  const [subscriber, setSubscriber] = useState("smo-gui");
  const [types, setTypes] = useState<string[]>(["SERVICE_API_AVAILABLE"]);
  const [cb, setCb] = useState("");
  const [apiIds, setApiIds] = useState("");
  const [invokerIds, setInvokerIds] = useState("");
  const [aefIds, setAefIds] = useState("");
  const base = PATHS.capifSubscriptions(subscriber);
  const listOrNull = (text: string) => (splitList(text).length ? splitList(text) : null);
  return (
    <Card section="data.capif-subscriptions" title="CAPIF event subscriptions">
      <div className="form inline">
        <Field label="Subscriber ID"><input value={subscriber} onChange={(e) => setSubscriber(e.target.value.trim())} /></Field>
        <Can method="POST" path={base}>
          <Field label="Events"><select multiple size={3} value={types} onChange={(e) => setTypes([...e.target.selectedOptions].map((o) => o.value))}>{EVENT_TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
          <Field label="Callback URI"><input value={cb} onChange={(e) => setCb(e.target.value)} placeholder="http://subscriber:8000/capif-events" /></Field>
          <Field label="Only these services" hint="serviceIds from Published services, comma-separated; empty = every service"><input value={apiIds} onChange={(e) => setApiIds(e.target.value)} placeholder="all services" /></Field>
          <Field label="Only these AEFs" hint="aefIds; matches service events only"><input value={aefIds} onChange={(e) => setAefIds(e.target.value)} placeholder="all AEFs" /></Field>
          <Field label="Only these invokers" hint="apiInvokerIds; matches invoker events only"><input value={invokerIds} onChange={(e) => setInvokerIds(e.target.value)} placeholder="all invokers" /></Field>
          <ActionButton label="Subscribe" disabled={!subscriber || !cb || types.length === 0} action={{ method: "POST", path: base, json: {
            subscriberId: subscriber, eventTypes: types, callbackUri: cb,
            apiIds: listOrNull(apiIds), aefIds: listOrNull(aefIds), apiInvokerIds: listOrNull(invokerIds) }, success: "Subscribed" }} />
        </Can>
      </div>
      <ServerTable<CapifEventSubscription> path={subscriber ? base : null} rowKey={(s) => s.subscriptionId} empty="No subscriptions for this subscriber." columns={[
        { header: "Subscription", render: (s) => <Id value={s.subscriptionId} /> }, { header: "Events", render: (s) => s.eventTypes.join(", ") },
        { header: "Filters", render: (s) => {
          const filters = [["services", s.apiIds], ["AEFs", s.aefIds], ["invokers", s.apiInvokerIds]] as const;
          const set = filters.filter(([, ids]) => ids?.length);
          return set.length ? set.map(([label, ids]) => <div key={label} className="small">{label}: <code>{ids!.join(", ")}</code></div>) : <span className="muted">none</span>;
        } },
        { header: "Callback", render: (s) => <code className="small">{s.callbackUri}</code> },
        { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `${base}/${s.subscriptionId}`, success: "Unsubscribed" }} /> },
      ]} />
    </Card>
  );
}
