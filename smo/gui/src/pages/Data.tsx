import { useState } from "react";

import { useSmo, useSmoAction } from "../api/hooks";
import type {
  CapifEventSubscription, DataJob, DataOffer, DmeType, DmeTypeSubscription, EiType, SmeInvoker, SmeProvider, SmeService,
  TrustedInvoker,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ActionButton, Can, Card, DataTable, Field, Id, Json, Modal, PageHeader, StateBadge, Tabs, useHashTab } from "../components/ui";
import { parseJsonObject, splitList } from "../lib/domain";

const TABS = ["dme", "a1-ei", "sme"] as const;
const DELIVERY_METHODS = ["PULL_HTTP", "PUSH_HTTP", "STREAMING_KAFKA"];
const EVENT_TYPES = ["SERVICE_API_AVAILABLE", "SERVICE_API_UNAVAILABLE", "SERVICE_API_UPDATE"];

export function Data() {
  const [tab, setTab] = useHashTab(TABS, "dme");
  return (
    <>
      <PageHeader title="Data & Exposure" subtitle={<>Data Management & Exposure (DME), A1 enrichment-information types, and Service Management & Exposure (SME / CAPIF) — call flows 01, 05 and 08</>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "dme", label: "DME: types, jobs, offers" }, { id: "a1-ei", label: "A1 EI types" }, { id: "sme", label: "SME: services & invokers" },
      ]} />
      {tab === "dme" && <Dme />}
      {tab === "a1-ei" && <EiTypes />}
      {tab === "sme" && <Sme />}
    </>
  );
}

// ---------------------------------------------------------------- DME

function Dme() {
  const types = useSmo<DmeType[]>("/dme/dme-types");
  const typeName = (id: string) => types.data?.find((t) => t.dmeTypeId === id)?.typeName ?? id.slice(0, 8);
  return (
    <>
      <Card title="Data types (production capabilities)" actions={<span className="muted small">Type status is a live call to each producer's health callback</span>}>
        <DataTable rows={types.data} loading={types.isLoading} error={types.error} rowKey={(t) => t.dmeTypeId} empty="No DME types registered." columns={[
          { header: "Type", render: (t) => <code>{t.typeName}</code> }, { header: "ID", render: (t) => <Id value={t.dmeTypeId} /> },
          { header: "Producer", render: (t) => t.producerId }, { header: "Status", render: (t) => <StateBadge state={t.typeStatus} /> },
          { header: "", className: "actions", render: (t) => <ActionButton label="Deregister producer" tone="danger" confirm={`Deregister producer ${t.producerId} and all its types, jobs and offers?`}
            action={{ method: "DELETE", path: "/dme/production-capabilities", query: { producer_id: t.producerId }, success: "Producer deregistered" }} /> },
        ]} />
        <Can method="POST" path="/dme/production-capabilities"><RegisterDmeType /></Can>
      </Card>
      <DataJobs types={types.data ?? []} typeName={typeName} />
      <Offers types={types.data ?? []} typeName={typeName} />
      <TypeSubscriptions />
    </>
  );
}

function RegisterDmeType() {
  const [f, setF] = useState({ namespace: "RAN", name: "", version: "1.0.0", producerId: "", healthUrl: "", jobUrl: "" });
  const [schema, setSchema] = useState('{"type": "object"}');
  const parsed = parseJsonObject(schema);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <details className="admin-tools">
      <summary>Admin: register a producer data type (what a producer rApp does)</summary>
      <div className="form grid cols-3 tight">
        <Field label="Namespace"><input value={f.namespace} onChange={set("namespace")} /></Field>
        <Field label="Name"><input value={f.name} onChange={set("name")} placeholder="CoverageIssue" /></Field>
        <Field label="Version"><input value={f.version} onChange={set("version")} /></Field>
        <Field label="Producer ID"><input value={f.producerId} onChange={set("producerId")} placeholder="rapp-producer-1" /></Field>
        <Field label="Health callback URL"><input value={f.healthUrl} onChange={set("healthUrl")} placeholder="http://producer:8000/health" /></Field>
        <Field label="Job callback URL"><input value={f.jobUrl} onChange={set("jobUrl")} placeholder="http://producer:8000/dme-jobs" /></Field>
        <Field label="Data production schema (JSON Schema)" hint={parsed.ok ? "Job definitions are validated against it" : <span className="text-bad">{parsed.error}</span>}>
          <textarea rows={2} value={schema} onChange={(e) => setSchema(e.target.value)} spellCheck={false} />
        </Field>
      </div>
      <ActionButton label="Register type" disabled={!parsed.ok || !f.name || !f.producerId || !f.healthUrl || !f.jobUrl} action={{
        method: "POST", path: "/dme/production-capabilities", success: "Type registered",
        json: { namespace: f.namespace, name: f.name, version: f.version, typeName: `${f.namespace}.${f.name}`, producerId: f.producerId,
          dataProductionSchema: parsed.ok ? parsed.value : {}, producerHealthCallbackUrl: f.healthUrl, jobCallbackUrl: f.jobUrl },
      }} />
    </details>
  );
}

function DataJobs({ types, typeName }: { types: DmeType[]; typeName: (id: string) => string }) {
  const { me } = useAuth();
  const jobs = useSmo<DataJob[]>("/dme/data-jobs");
  const offers = useSmo<DataOffer[]>("/dme/offers");
  const [typeId, setTypeId] = useState("");
  const [mode, setMode] = useState("CONTINUOUS");
  const [method, setMethod] = useState("PULL_HTTP");
  const [consumer, setConsumer] = useState(`smo-gui:${me?.username ?? ""}`);
  const [def, setDef] = useState("{}");
  const parsed = parseJsonObject(def);
  const [shown, setShown] = useState<DataJob | null>(null);
  const committed = [...new Set((offers.data ?? []).filter((o) => o.dmeTypeId === typeId && o.committedMethod).map((o) => o.committedMethod!))];
  return (
    <Card title="Data jobs (consumers)">
      <Can method="POST" path="/dme/data-jobs">
        <div className="form inline">
          <Field label="Type"><select value={typeId} onChange={(e) => {
            setTypeId(e.target.value);
            const c = offers.data?.find((o) => o.dmeTypeId === e.target.value && o.committedMethod)?.committedMethod;
            if (c) setMethod(c);
          }}><option value="">Choose…</option>{types.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}</select></Field>
          <Field label="Mode"><select value={mode} onChange={(e) => setMode(e.target.value)}><option>CONTINUOUS</option><option>ONE_TIME</option></select></Field>
          <Field label="Delivery" hint={typeId ? (committed.length ? `Committed by offers: ${committed.join(", ")}` : <span className="text-bad">No offer has committed a method for this type</span>) : undefined}>
            <select value={method} onChange={(e) => setMethod(e.target.value)}>{DELIVERY_METHODS.map((m) => <option key={m}>{m}</option>)}</select>
          </Field>
          <Field label="Consumer ID"><input value={consumer} onChange={(e) => setConsumer(e.target.value)} /></Field>
          <Field label="Job definition (JSON)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><input value={def} onChange={(e) => setDef(e.target.value)} /></Field>
          <ActionButton label="Create job" tone="primary" disabled={!typeId || !parsed.ok} action={{
            method: "POST", path: "/dme/data-jobs", success: "Data job created",
            json: { dmeTypeId: typeId, dataDeliveryMode: mode, dataDeliveryMethod: method, consumerId: consumer, productionJobDefinition: parsed.ok ? parsed.value : {} },
          }} />
        </div>
      </Can>
      <DataTable rows={jobs.data} loading={jobs.isLoading} error={jobs.error} rowKey={(j) => j.dataJobId} empty="No data jobs." onRowClick={setShown} columns={[
        { header: "Job", render: (j) => <Id value={j.dataJobId} /> }, { header: "Type", render: (j) => <code>{typeName(j.dmeTypeId)}</code> },
        { header: "Consumer", render: (j) => j.consumerId }, { header: "Mode / delivery", render: (j) => `${j.dataDeliveryMode} · ${j.dataDeliveryMethod}` },
        { header: "Status", render: (j) => <StateBadge state={j.status} /> },
        { header: "", className: "actions", render: (j) => <ActionButton label="Terminate" tone="danger" confirm="Terminate this data job? The producer is told to stop."
          action={{ method: "DELETE", path: `/dme/data-jobs/${j.dataJobId}`, success: "Data job terminated" }} /> },
      ]} />
      {shown && <Modal title={<>Data job <Id value={shown.dataJobId} /></>} onClose={() => setShown(null)}><Json value={shown} /></Modal>}
    </Card>
  );
}

function Offers({ types, typeName }: { types: DmeType[]; typeName: (id: string) => string }) {
  const offers = useSmo<DataOffer[]>("/dme/offers");
  const [typeId, setTypeId] = useState("");
  const [methods, setMethods] = useState<string[]>(["PUSH_HTTP", "PULL_HTTP"]);
  const [terminateUri, setTerminateUri] = useState("http://producer:8000/offer-terminated");
  return (
    <Card title="Data offers (producers)" actions={<span className="muted small">DME commits one of the offered methods (section 3.5)</span>}>
      <DataTable rows={offers.data} loading={offers.isLoading} error={offers.error} rowKey={(o) => o.offerId} empty="No data offers." columns={[
        { header: "Offer", render: (o) => <Id value={o.offerId} /> }, { header: "Type", render: (o) => <code>{typeName(o.dmeTypeId)}</code> },
        { header: "Offered", render: (o) => o.dataDeliveryMethodsOffered.join(", ") },
        { header: "Committed", render: (o) => o.committedMethod ? <span className="badge tone-ok">{o.committedMethod}</span> : <span className="muted">—</span> },
        { header: "", className: "actions", render: (o) => <div className="row gap end">
          <ActionButton label="Notify data ready" title="The producer's reversed-direction availability signal" action={{ method: "POST", path: `/dme/offers/${o.offerId}/notify`, json: { availableAt: new Date().toISOString() }, success: "Availability notified" }} />
          <ActionButton label="Terminate" tone="danger" confirm="Terminate this offer?" action={{ method: "DELETE", path: `/dme/offers/${o.offerId}`, success: "Offer terminated" }} />
        </div> },
      ]} />
      <Can method="POST" path="/dme/offers">
        <details className="admin-tools">
          <summary>Admin: create a producer offer</summary>
          <div className="form inline">
            <Field label="Type"><select value={typeId} onChange={(e) => setTypeId(e.target.value)}><option value="">Choose…</option>{types.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}</select></Field>
            <Field label="Methods offered" hint="Ctrl/Cmd-click for several"><select multiple size={3} value={methods} onChange={(e) => setMethods([...e.target.selectedOptions].map((o) => o.value))}>{DELIVERY_METHODS.map((m) => <option key={m}>{m}</option>)}</select></Field>
            <Field label="Termination notification URI"><input value={terminateUri} onChange={(e) => setTerminateUri(e.target.value)} /></Field>
          </div>
          <ActionButton label="Create offer" disabled={!typeId || methods.length === 0} action={{
            method: "POST", path: "/dme/offers", success: "Offer created",
            json: { dmeTypeId: typeId, dataDeliveryMode: "CONTINUOUS", dataDeliveryMethods: methods, dataOfferTerminationNotificationUri: terminateUri },
          }} />
        </details>
      </Can>
    </Card>
  );
}

function TypeSubscriptions() {
  const subs = useSmo<DmeTypeSubscription[]>("/dme/type-subscriptions");
  const [dest, setDest] = useState("");
  const [owner, setOwner] = useState("smo-gui");
  return (
    <Card title="Type subscriptions" actions={<span className="muted small">Notified whenever any type is registered or removed</span>}>
      <Can method="POST" path="/dme/type-subscriptions">
        <div className="form inline">
          <Field label="Notification destination"><input value={dest} onChange={(e) => setDest(e.target.value)} placeholder="http://consumer:8000/dme-types" /></Field>
          <Field label="Owner"><input value={owner} onChange={(e) => setOwner(e.target.value)} /></Field>
          <ActionButton label="Subscribe" disabled={!dest} action={{ method: "POST", path: "/dme/type-subscriptions", json: { notificationDestination: dest, owner }, success: "Subscribed to type changes" }} />
        </div>
      </Can>
      <DataTable rows={subs.data} rowKey={(s) => s.subscriptionId} empty="No type subscriptions." columns={[
        { header: "Subscription", render: (s) => <Id value={s.subscriptionId} /> }, { header: "Owner", render: (s) => s.owner },
        { header: "Destination", render: (s) => <code className="small">{s.notificationDestination}</code> },
        { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `/dme/type-subscriptions/${s.subscriptionId}`, success: "Unsubscribed" }} /> },
      ]} />
    </Card>
  );
}

// ---------------------------------------------------------------- A1 EI

function EiTypes() {
  const eiTypes = useSmo<EiType[]>("/a1-related/ei-types");
  const types = useSmo<DmeType[]>("/dme/dme-types");
  const [f, setF] = useState({ ei_type_id: "", registered_by: "", dme_namespace: "RAN", dme_name: "", dme_version: "1.0.0" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <Card title="A1 enrichment-information types" actions={<span className="muted small">RegisterEIType wraps DME's RegisterDMEType — no parallel registry (A1 Related LLD section 3)</span>}>
      <DataTable rows={eiTypes.data} loading={eiTypes.isLoading} error={eiTypes.error} rowKey={(t) => t.eiTypeId} empty="No EI types registered." columns={[
        { header: "EI type", render: (t) => <strong>{t.eiTypeId}</strong> }, { header: "Registered by", render: (t) => t.registeredBy },
        { header: "DME type", render: (t) => { const d = types.data?.find((x) => x.dmeTypeId === t.eiSourceDmeTypeId); return d ? <><code>{d.typeName}</code> <StateBadge state={d.typeStatus} /></> : <Id value={t.eiSourceDmeTypeId} />; } },
        { header: "", className: "actions", render: (t) => <ActionButton label="Deregister" tone="danger" confirm={`Deregister EI type ${t.eiTypeId}?`} action={{ method: "DELETE", path: `/a1-related/ei-types/${t.eiTypeId}`, success: "EI type deregistered" }} /> },
      ]} />
      <Can method="POST" path="/a1-related/ei-types/register">
        <details className="admin-tools">
          <summary>Admin: register an EI type (what an EI producer does, call flow 05)</summary>
          <div className="form grid cols-3 tight">
            <Field label="EI type ID"><input value={f.ei_type_id} onChange={set("ei_type_id")} placeholder="coverage-issue-ei" /></Field>
            <Field label="Registered by (producer)"><input value={f.registered_by} onChange={set("registered_by")} placeholder="rapp-ei-producer" /></Field>
            <Field label="DME namespace"><input value={f.dme_namespace} onChange={set("dme_namespace")} /></Field>
            <Field label="DME name"><input value={f.dme_name} onChange={set("dme_name")} placeholder="CoverageIssue" /></Field>
            <Field label="DME version"><input value={f.dme_version} onChange={set("dme_version")} /></Field>
          </div>
          <ActionButton label="Register EI type" disabled={!f.ei_type_id || !f.registered_by || !f.dme_name} action={{ method: "POST", path: "/a1-related/ei-types/register", query: f, success: "EI type registered (and its DME type)" }} />
        </details>
      </Can>
    </Card>
  );
}

// ---------------------------------------------------------------- SME

function Sme() {
  const providers = useSmo<SmeProvider[]>("/sme/provider-registrations");
  const [apf, setApf] = useState<string | null>(null);
  const active = apf ?? providers.data?.[0]?.apfId ?? null;
  const [newApf, setNewApf] = useState("");
  const [domain, setDomain] = useState("");
  return (
    <>
      <div className="grid cols-2">
        <Card title="API providers (publishing functions)">
          <DataTable rows={providers.data} loading={providers.isLoading} error={providers.error} rowKey={(p) => p.apfId} empty="No providers registered."
            onRowClick={(p) => setApf(p.apfId)} selectedKey={active} columns={[
              { header: "APF ID (== rAppId)", render: (p) => <strong>{p.apfId}</strong> }, { header: "Domain", render: (p) => p.providerDomainInfo ?? "—" },
              { header: "Services", render: (p) => p.serviceCount },
              { header: "", className: "actions", render: (p) => <ActionButton label="Deregister" tone="danger" confirm={`Deregister provider ${p.apfId}?`} action={{ method: "DELETE", path: `/sme/provider-registrations/${p.apfId}`, success: "Provider deregistered" }} /> },
            ]} />
          <Can method="POST" path="/sme/provider-registrations">
            <div className="form inline">
              <Field label="APF ID"><input value={newApf} onChange={(e) => setNewApf(e.target.value)} placeholder="rapp-1" /></Field>
              <Field label="Domain info"><input value={domain} onChange={(e) => setDomain(e.target.value)} /></Field>
              <ActionButton label="Register provider" disabled={!newApf} action={{ method: "POST", path: "/sme/provider-registrations", json: { apfId: newApf, providerDomainInfo: domain || null }, success: "Provider registered" }} />
            </div>
          </Can>
        </Card>
        <Invokers />
      </div>
      {active && <PublishedServices apfId={active} />}
      <Discovery />
      <EventSubscriptions />
    </>
  );
}

function PublishedServices({ apfId }: { apfId: string }) {
  const services = useSmo<SmeService[]>(`/sme/published-apis/v1/${apfId}/service-apis`);
  const [f, setF] = useState({ serviceName: "", endpoint: "", version: "1.0", moduleScope: "rapp", allowedConsumers: "" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const base = `/sme/published-apis/v1/${apfId}/service-apis`;
  return (
    <Card title={<>Service APIs published by <code>{apfId}</code></>}>
      <DataTable rows={services.data} loading={services.isLoading} error={services.error} rowKey={(s) => s.serviceId} empty="Nothing published." columns={[
        { header: "Service", render: (s) => <strong>{s.serviceName}</strong> }, { header: "Version", render: (s) => s.version },
        { header: "Endpoint", render: (s) => <code className="small">{s.endpoint}</code> },
        { header: "AEF profiles", render: (s) => s.aefProfiles.length },
        { header: "", className: "actions", render: (s) => <ActionButton label="Unpublish" tone="danger" confirm={`Unpublish ${s.serviceName}?`} action={{ method: "DELETE", path: `${base}/${s.serviceId}`, success: "Service unpublished (SERVICE_API_UNAVAILABLE sent)" }} /> },
      ]} />
      <Can method="POST" path={base}>
        <details className="admin-tools">
          <summary>Admin: publish a service API for {apfId}</summary>
          <div className="form grid cols-3 tight">
            <Field label="Service name" hint="Globally unique"><input value={f.serviceName} onChange={set("serviceName")} /></Field>
            <Field label="Endpoint"><input value={f.endpoint} onChange={set("endpoint")} placeholder="http://rapp-1:8000/api" /></Field>
            <Field label="Version"><input value={f.version} onChange={set("version")} /></Field>
            <Field label="Module scope"><input value={f.moduleScope} onChange={set("moduleScope")} /></Field>
            <Field label="Allowed consumers" hint="Comma-separated invoker ids; empty = everyone"><input value={f.allowedConsumers} onChange={set("allowedConsumers")} /></Field>
          </div>
          <ActionButton label="Publish" disabled={!f.serviceName || !f.endpoint} action={{
            method: "POST", path: base, success: "Service published (SERVICE_API_AVAILABLE sent)",
            json: { serviceName: f.serviceName, producerId: apfId, endpoint: f.endpoint, version: f.version, moduleScope: f.moduleScope, allowedConsumers: splitList(f.allowedConsumers) },
          }} />
        </details>
      </Can>
    </Card>
  );
}

function Invokers() {
  const invokers = useSmo<SmeInvoker[]>("/sme/invoker-registrations");
  const trusted = useSmo<TrustedInvoker[]>("/sme/trusted-invokers");
  const onboard = useSmoAction();
  const [publicKey, setPublicKey] = useState("");
  const [secret, setSecret] = useState<{ apiInvokerId: string; onboardingSecret: string } | null>(null);
  const [trustFor, setTrustFor] = useState<string | null>(null);
  return (
    <Card title="API invokers">
      <DataTable rows={invokers.data} loading={invokers.isLoading} error={invokers.error} rowKey={(i) => i.apiInvokerId} empty="No invokers onboarded." columns={[
        { header: "Invoker", render: (i) => <code className="small">{i.apiInvokerId}</code> },
        { header: "Trusted", render: (i) => i.trusted ? <StateBadge state="ENABLED" /> : <span className="muted">no</span> },
        { header: "", className: "actions", render: (i) => i.trusted
          ? <ActionButton label="Remove trust" tone="danger" confirm="Remove this invoker's security context?" action={{ method: "DELETE", path: `/sme/trusted-invokers/${i.apiInvokerId}`, success: "Security context removed" }} />
          : <Can method="PUT" path={`/sme/trusted-invokers/${i.apiInvokerId}`}><button className="btn small" onClick={() => setTrustFor(i.apiInvokerId)}>Trust…</button></Can> },
      ]} />
      <p className="muted small">{trusted.data?.length ?? 0} with a security context. Onboarding secrets are hashed at SME and never shown again.</p>
      <Can method="POST" path="/sme/invoker-registrations">
        <div className="form inline">
          <Field label="Invoker public key"><input value={publicKey} onChange={(e) => setPublicKey(e.target.value)} placeholder="-----BEGIN PUBLIC KEY-----…" /></Field>
          <button className="btn" disabled={!publicKey || onboard.isPending} onClick={() => onboard.mutate(
            { method: "POST", path: "/sme/invoker-registrations", json: { apiInvokerPublicKey: publicKey }, success: "Invoker onboarded" },
            { onSuccess: (d) => { setSecret(d as { apiInvokerId: string; onboardingSecret: string }); setPublicKey(""); } })}>Onboard invoker</button>
        </div>
      </Can>
      {secret && (
        <Modal title="Invoker onboarded — copy the secret now" onClose={() => setSecret(null)}>
          <p>SME stores only a hash of this onboarding secret. It is shown once; the invoker uses it for the OAuth2 client_credentials grant.</p>
          <dl className="kv"><div><dt>apiInvokerId</dt><dd><code>{secret.apiInvokerId}</code></dd></div><div><dt>onboardingSecret</dt><dd><code>{secret.onboardingSecret}</code></dd></div></dl>
        </Modal>
      )}
      {trustFor && <TrustInvoker invokerId={trustFor} onClose={() => setTrustFor(null)} />}
    </Card>
  );
}

function TrustInvoker({ invokerId, onClose }: { invokerId: string; onClose: () => void }) {
  const [dest, setDest] = useState("http://invoker:8000/security-notify");
  const [aefId, setAefId] = useState("");
  const [apiId, setApiId] = useState("");
  const [method, setMethod] = useState("OAUTH");
  const action = useSmoAction();
  return (
    <Modal title="Register trusted-invoker security context" onClose={onClose}>
      <form className="form" onSubmit={(e) => {
        e.preventDefault();
        action.mutate({ method: "PUT", path: `/sme/trusted-invokers/${invokerId}`, success: "Security context registered",
          json: { notificationDestination: dest, securityInfo: [{ aefId: aefId || null, apiId: apiId || null, prefSecurityMethods: [method] }] } }, { onSuccess: onClose });
      }}>
        <p className="muted small">Invoker <code>{invokerId}</code></p>
        <Field label="Notification destination"><input value={dest} onChange={(e) => setDest(e.target.value)} required /></Field>
        <div className="grid cols-3 tight">
          <Field label="AEF ID"><input value={aefId} onChange={(e) => setAefId(e.target.value)} /></Field>
          <Field label="API ID"><input value={apiId} onChange={(e) => setApiId(e.target.value)} /></Field>
          <Field label="Preferred method"><select value={method} onChange={(e) => setMethod(e.target.value)}><option>OAUTH</option><option>PSK</option><option>PKI</option></select></Field>
        </div>
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={action.isPending}>Register</button></div>
      </form>
    </Modal>
  );
}

function Discovery() {
  const invokers = useSmo<SmeInvoker[]>("/sme/invoker-registrations");
  const [invoker, setInvoker] = useState("");
  const [apiName, setApiName] = useState("");
  const found = useSmo<SmeService[]>(invoker ? "/sme/service-apis/v1/allServiceAPIs" : null, { api_invoker_id: invoker, api_name: apiName });
  return (
    <Card title="Service discovery (as an invoker sees it)" actions={<span className="muted small">allowedConsumers hides services from invokers not listed</span>}>
      <div className="form inline">
        <Field label="Discover as invoker"><select value={invoker} onChange={(e) => setInvoker(e.target.value)}><option value="">Choose…</option>{invokers.data?.map((i) => <option key={i.apiInvokerId} value={i.apiInvokerId}>{i.apiInvokerId}</option>)}</select></Field>
        <Field label="API name filter"><input value={apiName} onChange={(e) => setApiName(e.target.value)} /></Field>
      </div>
      {invoker && <DataTable rows={found.data} loading={found.isLoading} error={found.error} rowKey={(s) => s.serviceId} empty="No services visible to this invoker." columns={[
        { header: "Service", render: (s) => <strong>{s.serviceName}</strong> }, { header: "Producer", render: (s) => s.producerId },
        { header: "Version", render: (s) => s.version }, { header: "Endpoint", render: (s) => <code className="small">{s.endpoint}</code> },
      ]} />}
    </Card>
  );
}

function EventSubscriptions() {
  const [subscriber, setSubscriber] = useState("smo-gui");
  const subs = useSmo<CapifEventSubscription[]>(subscriber ? `/sme/capif-events/v1/${subscriber}/subscriptions` : null);
  const [types, setTypes] = useState<string[]>(["SERVICE_API_AVAILABLE"]);
  const [cb, setCb] = useState("");
  const [apiIds, setApiIds] = useState("");
  const base = `/sme/capif-events/v1/${subscriber}/subscriptions`;
  return (
    <Card title="CAPIF event subscriptions">
      <div className="form inline">
        <Field label="Subscriber ID"><input value={subscriber} onChange={(e) => setSubscriber(e.target.value.trim())} /></Field>
        <Can method="POST" path={base}>
          <Field label="Events"><select multiple size={3} value={types} onChange={(e) => setTypes([...e.target.selectedOptions].map((o) => o.value))}>{EVENT_TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
          <Field label="Callback URI"><input value={cb} onChange={(e) => setCb(e.target.value)} placeholder="http://subscriber:8000/capif-events" /></Field>
          <Field label="Only these services" hint="serviceIds from Published services, comma-separated; empty = every service"><input value={apiIds} onChange={(e) => setApiIds(e.target.value)} placeholder="all services" /></Field>
          <ActionButton label="Subscribe" disabled={!subscriber || !cb || types.length === 0} action={{ method: "POST", path: base, json: { subscriberId: subscriber, eventTypes: types, callbackUri: cb, apiIds: splitList(apiIds).length ? splitList(apiIds) : null }, success: "Subscribed" }} />
        </Can>
      </div>
      <DataTable rows={subs.data} rowKey={(s) => s.subscriptionId} empty="No subscriptions for this subscriber." columns={[
        { header: "Subscription", render: (s) => <Id value={s.subscriptionId} /> }, { header: "Events", render: (s) => s.eventTypes.join(", ") },
        { header: "Services", render: (s) => s.apiIds?.length ? s.apiIds.map((id) => <code key={id} className="small">{id} </code>) : <span className="muted">all</span> },
        { header: "Callback", render: (s) => <code className="small">{s.callbackUri}</code> },
        { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `${base}/${s.subscriptionId}`, success: "Unsubscribed" }} /> },
      ]} />
    </Card>
  );
}
