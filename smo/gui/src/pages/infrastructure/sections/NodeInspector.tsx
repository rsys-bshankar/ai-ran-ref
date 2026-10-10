/** Infrastructure → Topology, the inspector (`infrastructure.inspector`): the selected topology node's kind, health and why, its TEIV attributes,
 * what sits under it and what runs on it, and, for a resource, its CPU and memory utilisation from FOCOM (`GET /focom/resources/{id}/utilisation`,
 * GUI-9.8b: the newest CPU_UTILIZATION / MEMORY_UTILIZATION performance records; "—" when none was ingested). GPU utilisation is measured by no
 * module and stays "—"; the other kinds are not measured (utilisation is per resource). A resource can be
 * deprovisioned from here (the same role-gated call as the inventory tab); a workload links to the NF deployments tab, where its actions live. */
import type { ReactNode } from "react";

import { ActionButton, Card, KeyValue } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { Empty } from "../../../kit/states";
import { formatTime } from "../../../lib/domain";
import { formatPercent, useResourceUtilisation } from "../data/queries";
import { HEALTH_LABEL, HEALTH_TONE, KIND_LABEL } from "../data/topology";
import type { TopoTree } from "../data/types";

/** Renders an attribute value: text as is, null as "—", anything else as JSON. */
function show(v: unknown) {
  if (v === null || v === undefined || v === "") return <span className="muted">—</span>;
  return typeof v === "object" ? <code className="small">{JSON.stringify(v)}</code> : String(v);
}

/** The utilisation block of a node: a resource's CPU and memory (one FOCOM read while it is selected); any other kind says it is not measured. */
function UtilisationOf({ kind, resourceId }: { kind: string; resourceId: string }) {
  const u = useResourceUtilisation(kind === "resource" ? resourceId : null);
  if (kind !== "resource") return <p className="small muted">Measured per resource: select a resource to see its CPU and memory.</p>;
  const d = u.data;
  return (
    <>
      <KeyValue items={[["CPU", formatPercent(d?.cpuPercent)], ["Memory", formatPercent(d?.memoryPercent)], ["GPU", "—"], ["Measured", d?.at ? formatTime(d.at) : "—"]]} />
      {u.error && <p className="small t-warn">Utilisation unavailable: {u.error.message}</p>}
      {d && d.cpuPercent === null && d.memoryPercent === null && <p className="gap-note">FOCOM holds no CPU_UTILIZATION or MEMORY_UTILIZATION record for this resource yet.</p>}
      <p className="gap-note">GPU utilisation is measured by no module.</p>
    </>
  );
}

/** The inspector. */
export function NodeInspector({ tree, selected }: { tree: TopoTree | null; selected: string | null }) {
  const n = selected ? tree?.nodes.get(selected) : undefined;
  if (!tree || !n) {
    return <Card section="infrastructure.inspector" title="Inspector"><Empty title="Select a node.">Click a tile to see its attributes and what runs on it.</Empty></Card>;
  }
  const kids = n.children.map((id) => tree.nodes.get(id)!);
  const workloads = kids.filter((k) => k.kind === "workload");
  const parent = n.parent ? tree.nodes.get(n.parent) : undefined;
  return (
    <Card section="infrastructure.inspector" title={<span className="mono">{n.name}</span>} sub={KIND_LABEL[n.kind]}
      actions={<Badge tone={HEALTH_TONE[n.health]}>{HEALTH_LABEL[n.health]}</Badge>}>
      <p className="small muted">{n.healthWhy}</p>
      <KeyValue items={[
        ["ID", <code className="small">{n.ref}</code>],
        ["Under", parent ? `${KIND_LABEL[parent.kind]} ${parent.name}` : "—"],
        ...Object.entries(n.attributes).filter(([k]) => k !== "name").slice(0, 12).map(([k, v]) => [k, show(v)] as [string, ReactNode]),
      ]} />
      <div className="eyebrow">Utilisation</div>
      <UtilisationOf kind={n.kind} resourceId={n.ref} />
      {kids.length > 0 && <>
        <div className="eyebrow">Under it · {kids.length}</div>
        <KeyValue items={(["dm", "pool", "resource", "workload"] as const).map((k) => [KIND_LABEL[k], kids.filter((c) => c.kind === k).length] as [string, number]).filter(([, c]) => c > 0)} />
      </>}
      {n.kind === "ocloud" && <>
        <div className="eyebrow">What runs on it · {workloads.length}</div>
        {workloads.length ? (
          <div className="row wrap">
            {workloads.slice(0, 10).map((w) => <span key={w.id} className="chip">{w.name} <Badge tone={HEALTH_TONE[w.health]} plain>{w.state}</Badge></span>)}
            {workloads.length > 10 && <a className="chip" href="#nfo">+ {workloads.length - 10} more</a>}
          </div>
        ) : <span className="small muted">No NF deployment is placed on this O-Cloud.</span>}
      </>}
      {(n.kind === "resource" || n.kind === "pool") && <p className="gap-note">NFO places a workload on the O-Cloud, not on a resource, so what runs on a single {n.kind} is not known.</p>}
      {n.kind === "workload" && <a className="btn small" href="#nfo">Open in NF deployments</a>}
      {n.kind === "resource" && (
        <div className="row end">
          <ActionButton label="Deprovision" tone="danger" confirm="Deprovision this resource?" action={{ method: "DELETE", path: `/focom/resources/${n.ref}`, success: "Resource deprovisioned" }} />
        </div>
      )}
    </Card>
  );
}
