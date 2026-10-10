/** Data & Exposure → Producers & offers, the producer and type boxes: registered producers (`data.producers`, deregister) and the data types they
 * produce (`data.types`, status, delete, and the admin "register a producer data type" form). Both routes are unpaged registries in DME, so the
 * tables are client tables of the whole (small) list. Moved from the pre-redesign page with its actions unchanged. */
import { useState } from "react";

import { ActionButton, Can, Card, DataTable, Field, Id, StateBadge } from "../../../components/ui";
import { parseJsonObject } from "../../../lib/domain";
import { PATHS, useDmeTypes, useProducers } from "../data/queries";

/** The producers box. */
export function Producers() {
  const producers = useProducers();
  const types = useDmeTypes();
  const typeName = (id: string) => types.data?.find((t) => t.dmeTypeId === id)?.typeName ?? id.slice(0, 8);
  return (
    <Card section="data.producers" title="Producers" sub="A producer is its own entity now (HISTORY.md §7) — several may support the same type">
      <DataTable rows={producers.data} loading={producers.isLoading} error={producers.error} rowKey={(p) => p.producerId} empty="No producers registered." columns={[
        { header: "Producer", render: (p) => <code>{p.producerId}</code> }, { header: "Health callback", render: (p) => p.producerHealthCallbackUrl },
        { header: "Job callback", render: (p) => p.jobCallbackUrl }, { header: "Supported types", render: (p) => p.supportedTypeIds.map(typeName).join(", ") || "—" },
        { header: "", className: "actions", render: (p) => <ActionButton label="Deregister" tone="danger"
          confirm={`Deregister producer ${p.producerId}? Its types stay registered (served by any other producer, or DISABLED if none).`}
          action={{ method: "DELETE", path: PATHS.producers, query: { producer_id: p.producerId }, success: "Producer deregistered" }} /> },
      ]} />
    </Card>
  );
}

/** The data types box. */
export function DataTypes() {
  const types = useDmeTypes();
  return (
    <Card section="data.types" title="Data types (production capabilities)" sub="Type status is ENABLED if any of its producers answers its health callback">
      <DataTable rows={types.data} loading={types.isLoading} error={types.error} rowKey={(t) => t.dmeTypeId} empty="No DME types registered." columns={[
        { header: "Type", render: (t) => <code>{t.typeName}</code> }, { header: "ID", render: (t) => <Id value={t.dmeTypeId} /> },
        { header: "Producers", render: (t) => t.producerIds.join(", ") || "—" }, { header: "Status", render: (t) => <StateBadge state={t.typeStatus} /> },
        { header: "", className: "actions", render: (t) => <ActionButton label="Delete type" tone="danger" confirm={`Delete type ${t.typeName}? Fails if any producer still supports it.`}
          action={{ method: "DELETE", path: `${PATHS.types}/${t.dmeTypeId}`, success: "Type deleted" }} /> },
      ]} />
      <Can method="POST" path={PATHS.producers}><RegisterDmeType /></Can>
    </Card>
  );
}

/** The admin form that registers a producer and one data type it produces (what a producer rApp does). */
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
        method: "POST", path: PATHS.producers, success: "Type registered",
        json: { namespace: f.namespace, name: f.name, version: f.version, typeName: `${f.namespace}.${f.name}`, producerId: f.producerId,
          dataProductionSchema: parsed.ok ? parsed.value : {}, producerHealthCallbackUrl: f.healthUrl, jobCallbackUrl: f.jobUrl },
      }} />
    </details>
  );
}
