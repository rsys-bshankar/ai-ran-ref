/** Infrastructure → O-Cloud inventory (`infrastructure.inventory`, BRIEF §4e feature 6): a level picker from where the O-Cloud is to what it
 * holds (locations → sites → node clusters → cluster resources → infrastructure resources), then provisioning requests and performance jobs, then
 * the O2-IMS pools, deployment managers and resource types. One level is listed at a time, as a server-paged table of that level's FOCOM route.
 * Picking a resource pool lists its resources below (`infrastructure.pool-resources`), with their CPU and memory utilisation (one batched FOCOM
 * read for the page shown, `GET /focom/utilisation?resource_ids=`, GUI-9.8b), deprovision and the admin "provision a resource". */
import { useCallback, useState } from "react";

import type { OCloudResource, ResourcePool } from "../../../api/types";
import { ActionButton, Can, Card, Field, Id } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { parseJsonObject } from "../../../lib/domain";
import { formatPercent, INVENTORY_LEVELS, INVENTORY_POLL, PATHS, useUtilisationBatch } from "../data/queries";
import { LEVEL_SPECS } from "./InventoryColumns";

const GROUP_LABEL = { place: "Where it is", work: "Work on it", ims: "O2-IMS inventory" } as const;

/** The picker and the chosen level's table. */
export function OCloudInventory() {
  const [levelId, setLevelId] = useState(INVENTORY_LEVELS[0].id);
  const [pool, setPool] = useState<string | null>(null);
  const level = INVENTORY_LEVELS.find((l) => l.id === levelId)!;
  const spec = LEVEL_SPECS[level.id];
  return (
    <>
      <Card section="infrastructure.inventory" title="O-Cloud inventory" sub="FOCOM · O2ims · from where it is to what runs on it · pick a level">
        <div className="stack" style={{ gap: 8 }}>
          {(Object.keys(GROUP_LABEL) as (keyof typeof GROUP_LABEL)[]).map((g) => (
            <div key={g} className="row wrap" role="group" aria-label={GROUP_LABEL[g]}>
              <span className="eyebrow inv-group">{GROUP_LABEL[g]}</span>
              {INVENTORY_LEVELS.filter((l) => l.group === g).map((l, i, all) => (
                <span key={l.id} className="row">
                  <button type="button" className={`btn small${l.id === levelId ? " primary" : ""}`} aria-pressed={l.id === levelId} onClick={() => { setLevelId(l.id); setPool(null); }}>{l.label}</button>
                  {g === "place" && i < all.length - 1 && <span className="muted" aria-hidden>→</span>}
                </span>
              ))}
            </div>
          ))}
        </div>
        <ServerTable<never> key={level.id} path={level.path} rowKey={spec.rowKey} columns={spec.columns} refetchInterval={INVENTORY_POLL}
          empty={`No ${level.label.toLowerCase()} recorded.`}
          onRowClick={level.id === "pools" ? (r) => setPool((r as unknown as ResourcePool).resourcePoolId) : undefined} selectedKey={level.id === "pools" ? pool : undefined} />
        {level.id === "pools" && !pool && <p className="small muted">Pick a pool to list its resources.</p>}
      </Card>
      {level.id === "pools" && pool && <PoolResources pool={pool} />}
    </>
  );
}

/** The resources of one pool, with deprovision and the admin provisioning form. */
function PoolResources({ pool }: { pool: string }) {
  const [spec, setSpec] = useState('{"resourceTypeId": "", "description": "GPU node"}');
  const parsed = parseJsonObject(spec);
  const [shown, setShown] = useState<string[]>([]);
  const onRows = useCallback((rows: OCloudResource[]) => setShown(rows.map((r) => r.resourceId)), []);
  const util = useUtilisationBatch(shown);
  const of = (id: string) => util.data?.find((u) => u.resourceId === id);
  return (
    <Card section="infrastructure.pool-resources" title={<>Resources in pool <code>{pool}</code></>}>
      <ServerTable<OCloudResource> path={PATHS.poolResources(pool)} rowKey={(r) => r.resourceId} empty="No resources in this pool." refetchInterval={INVENTORY_POLL} onRows={onRows} columns={[
        { header: "Resource", render: (r) => <Id value={r.resourceId} /> },
        { header: "Type", render: (r) => r.resourceTypeId }, { header: "Description", render: (r) => r.description ?? "—" },
        { header: "Parent", render: (r) => <Id value={r.parentId} /> },
        { header: "CPU", render: (r) => <span className="num" title={of(r.resourceId)?.at ?? "no measurement"}>{formatPercent(of(r.resourceId)?.cpuPercent)}</span> },
        { header: "Memory", render: (r) => <span className="num">{formatPercent(of(r.resourceId)?.memoryPercent)}</span> },
        { header: "", className: "actions", render: (r) => <ActionButton label="Deprovision" tone="danger" confirm="Deprovision this resource?" action={{ method: "DELETE", path: `/focom/resources/${r.resourceId}`, success: "Resource deprovisioned" }} /> },
      ]} />
      <Can method="POST" path="/focom/resources/provision">
        <details className="admin-tools">
          <summary>Admin: provision a resource</summary>
          <Field label="Spec (JSON)" hint={parsed.ok ? "An unknown resourceTypeId is auto-registered." : <span className="text-bad">{parsed.error}</span>}><textarea rows={2} value={spec} onChange={(e) => setSpec(e.target.value)} spellCheck={false} /></Field>
          <ActionButton label="Provision" disabled={!parsed.ok} action={{ method: "POST", path: "/focom/resources/provision", json: parsed.ok ? parsed.value : {}, success: "Resource provisioned" }} />
        </details>
      </Can>
    </Card>
  );
}
