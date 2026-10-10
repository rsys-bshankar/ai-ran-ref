/** Data & Exposure → Flow & jobs, the data jobs box (`data.jobs`): the role-gated "create a data job" form (the delivery methods the type's
 * offers committed are suggested), and the server-paged job table filtered by type (`?dme_type_id=`) and consumer (`?consumer_id=`), with
 * a "late only" filter (`?late=true`: two declared intervals passed without a delivery), the last delivery time with a LATE badge, terminate and
 * a JSON view of a job. */
import { useState } from "react";

import type { DataJob } from "../../../api/types";
import { useAuth } from "../../../auth/AuthContext";
import { ActionButton, Can, Card, Field, Id, Json, Modal, StateBadge } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { Badge } from "../../../kit/Badge";
import { formatTime, parseJsonObject } from "../../../lib/domain";
import { PATHS, useDmeTypes, useOffersForType } from "../data/queries";

/** The delivery methods DME knows. */
export const DELIVERY_METHODS = ["PULL_HTTP", "PUSH_HTTP", "STREAMING_KAFKA"];

/** The box. */
export function DataJobs() {
  const types = useDmeTypes();
  const typeName = (id: string) => types.data?.find((t) => t.dmeTypeId === id)?.typeName ?? id.slice(0, 8);
  const [typeFilter, setTypeFilter] = useState("");
  const [consumerFilter, setConsumerFilter] = useState("");
  const [lateOnly, setLateOnly] = useState(false);
  const [shown, setShown] = useState<DataJob | null>(null);
  return (
    <Card section="data.jobs" title="Data jobs (consumers)" actions={<>
      <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} aria-label="Filter by type"><option value="">Any type</option>{types.data?.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}</select>
      <label className="search" style={{ width: 220 }}><input aria-label="Filter by consumer" placeholder="Consumer ID (exact)" value={consumerFilter} onChange={(e) => setConsumerFilter(e.target.value.trim())} /></label>
      <label className="check"><input type="checkbox" checked={lateOnly} onChange={(e) => setLateOnly(e.target.checked)} /> late only</label>
    </>}>
      <Can method="POST" path={PATHS.dataJobs}><CreateJob /></Can>
      <ServerTable<DataJob> path={PATHS.dataJobs} query={{ dme_type_id: typeFilter || undefined, consumer_id: consumerFilter || undefined, late: lateOnly ? true : undefined }} rowKey={(j) => j.dataJobId}
        empty="No data jobs." onRowClick={setShown} columns={[
          { header: "Job", render: (j) => <Id value={j.dataJobId} /> }, { header: "Type", render: (j) => <code>{typeName(j.dmeTypeId)}</code> },
          { header: "Consumer", render: (j) => j.consumerId }, { header: "Mode / delivery", render: (j) => `${j.dataDeliveryMode} · ${j.dataDeliveryMethod}` },
          { header: "Last delivery", render: (j) => <LastDelivery job={j} /> },
          { header: "Status", render: (j) => <StateBadge state={j.status} /> },
          { header: "", className: "actions", render: (j) => <ActionButton label="Terminate" tone="danger" confirm="Terminate this data job? The producer is told to stop."
            action={{ method: "DELETE", path: `${PATHS.dataJobs}/${j.dataJobId}`, success: "Data job terminated" }} /> },
        ]} />
      {shown && <Modal title={<>Data job <Id value={shown.dataJobId} /></>} onClose={() => setShown(null)}><Json value={shown} /></Modal>}
    </Card>
  );
}

/** When the job's producer last delivered, and LATE when two declared intervals passed without a delivery ("no interval" when none is declared). */
function LastDelivery({ job }: { job: DataJob }) {
  const interval = job.expectedIntervalSeconds;
  return (
    <span className="row gap" title={interval ? `expected every ${interval} s` : "no delivery interval declared, so lateness is not judged"}>
      <span className={job.lastDeliveryAt ? "small" : "small muted"}>{job.lastDeliveryAt ? formatTime(job.lastDeliveryAt) : "never"}</span>
      {job.late === true && <Badge tone="bad">LATE</Badge>}
    </span>
  );
}

/** The create-job form. */
function CreateJob() {
  const { me } = useAuth();
  const types = useDmeTypes();
  const [typeId, setTypeId] = useState("");
  const offers = useOffersForType(typeId);
  const [mode, setMode] = useState("CONTINUOUS");
  const [method, setMethod] = useState("PULL_HTTP");
  const [consumer, setConsumer] = useState(`smo-gui:${me?.username ?? ""}`);
  const [def, setDef] = useState("{}");
  const parsed = parseJsonObject(def);
  const committed = [...new Set((offers.data ?? []).filter((o) => o.committedMethod).map((o) => o.committedMethod!))];
  // Once the chosen type's offers arrive, preselect the method one of them committed.
  const [suggestedFor, setSuggestedFor] = useState("");
  if (typeId && offers.data && suggestedFor !== typeId) {
    setSuggestedFor(typeId);
    if (committed[0]) setMethod(committed[0]);
  }
  return (
    <div className="form inline">
      <Field label="Type"><select value={typeId} onChange={(e) => setTypeId(e.target.value)}><option value="">Choose…</option>{types.data?.map((t) => <option key={t.dmeTypeId} value={t.dmeTypeId}>{t.typeName}</option>)}</select></Field>
      <Field label="Mode"><select value={mode} onChange={(e) => setMode(e.target.value)}><option>CONTINUOUS</option><option>ONE_TIME</option></select></Field>
      <Field label="Delivery" hint={typeId && offers.data ? (committed.length ? `Committed by offers: ${committed.join(", ")}` : <span className="text-bad">No offer has committed a method for this type</span>) : undefined}>
        <select value={method} onChange={(e) => setMethod(e.target.value)}>{DELIVERY_METHODS.map((m) => <option key={m}>{m}</option>)}</select>
      </Field>
      <Field label="Consumer ID"><input value={consumer} onChange={(e) => setConsumer(e.target.value)} /></Field>
      <Field label="Job definition (JSON)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><input value={def} onChange={(e) => setDef(e.target.value)} /></Field>
      <ActionButton label="Create job" tone="primary" disabled={!typeId || !parsed.ok} action={{
        method: "POST", path: PATHS.dataJobs, success: "Data job created",
        json: { dmeTypeId: typeId, dataDeliveryMode: mode, dataDeliveryMethod: method, consumerId: consumer, productionJobDefinition: parsed.ok ? parsed.value : {} },
      }} />
    </div>
  );
}
