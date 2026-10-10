/** Data & Exposure → SME, the exposed-services boxes: API providers (`data.providers`, server-paged; register, deregister; a row picks the
 * provider) and the service APIs the picked provider published (`data.services`: unpublish, the admin publish form). The published-services
 * route is not paged and takes no search, so it is a client table of that one provider's services; search across providers is the discovery box. */
import { useCallback, useState } from "react";

import type { SmeProvider } from "../../../api/types";
import { ActionButton, Can, Card, DataTable, Field } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { splitList } from "../../../lib/domain";
import { PATHS, usePublishedServices } from "../data/queries";

/** The providers box and, under it, the picked provider's services. The first provider of the page is picked until the operator picks one. */
export function ExposedServices() {
  const [apf, setApf] = useState<string | null>(null);
  const [first, setFirst] = useState<string | null>(null);
  const onRows = useCallback((rows: SmeProvider[]) => setFirst(rows[0]?.apfId ?? null), []);
  const active = apf ?? first;
  const [newApf, setNewApf] = useState("");
  const [domain, setDomain] = useState("");
  return (
    <>
      <Card section="data.providers" title="API providers (publishing functions)">
        <ServerTable<SmeProvider> path={PATHS.providers} rowKey={(p) => p.apfId} empty="No providers registered." onRows={onRows}
          onRowClick={(p) => setApf(p.apfId)} selectedKey={active} columns={[
            { header: "APF ID (== rAppId)", render: (p) => <strong>{p.apfId}</strong> }, { header: "Domain", render: (p) => p.providerDomainInfo ?? "—" },
            { header: "Services", render: (p) => p.serviceCount },
            { header: "", className: "actions", render: (p) => <ActionButton label="Deregister" tone="danger" confirm={`Deregister provider ${p.apfId}?`} action={{ method: "DELETE", path: `${PATHS.providers}/${p.apfId}`, success: "Provider deregistered" }} /> },
          ]} />
        <Can method="POST" path={PATHS.providers}>
          <div className="form inline">
            <Field label="APF ID"><input value={newApf} onChange={(e) => setNewApf(e.target.value)} placeholder="rapp-1" /></Field>
            <Field label="Domain info"><input value={domain} onChange={(e) => setDomain(e.target.value)} /></Field>
            <ActionButton label="Register provider" disabled={!newApf} action={{ method: "POST", path: PATHS.providers, json: { apfId: newApf, providerDomainInfo: domain || null }, success: "Provider registered" }} />
          </div>
        </Can>
      </Card>
      {active && <PublishedServices apfId={active} />}
    </>
  );
}

/** The services one provider published, and the admin publish form. */
function PublishedServices({ apfId }: { apfId: string }) {
  const services = usePublishedServices(apfId);
  const [f, setF] = useState({ serviceName: "", endpoint: "", version: "1.0", moduleScope: "rapp", allowedConsumers: "" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const base = PATHS.publishedServices(apfId);
  return (
    <Card section="data.services" title={<>Service APIs published by <code>{apfId}</code></>} sub="SME · CAPIF">
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
