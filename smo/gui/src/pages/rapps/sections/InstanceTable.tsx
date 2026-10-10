/** The rApps page's main table (SCALE.md P1): every rApp instance, paged by rApp Management (`GET /rapp-mgmt/instances?state=`), with a flow 07
 * lifecycle column, the package name, autonomy mode, the headline KPI (one batched read of the page's newest metrics,
 * `/rapp-mgmt/instances/performance/latest?ids=`) and the lifecycle actions. A row opens the instance drawer. Section id `rapps.instances`. */
import { useCallback, useState } from "react";
import { Link } from "react-router-dom";

import type { InstanceSummary } from "../../../api/types";
import { Card, Id, StateBadge } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { INSTANCE_STATES, INSTANCES_PATH, useLatestKpis, usePackageNames } from "../data/queries";
import { HeadlineKpi } from "./HeadlineKpi";
import { InstanceActions } from "./InstanceActions";
import { InstanceDrawer } from "./InstanceDrawer";
import { LifecycleCell } from "./LifecycleCell";

/** The instance table with its state filter. */
export function InstanceTable() {
  const [state, setState] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const names = usePackageNames();
  const [ids, setIds] = useState<string[]>([]);
  const kpis = useLatestKpis(ids);
  const onRows = useCallback((rows: InstanceSummary[]) => setIds(rows.map((r) => r.instanceId)), []);
  return (
    <Card section="rapps.instances" title="rApp instances" sub="paged by rApp Management · click a row for details"
      actions={
        <select value={state} onChange={(e) => setState(e.target.value)} aria-label="Filter by state">
          <option value="">All states</option>
          {INSTANCE_STATES.map((s) => <option key={s}>{s}</option>)}
        </select>}>
      <ServerTable<InstanceSummary> path={INSTANCES_PATH} query={{ state: state || undefined }} rowKey={(i) => i.instanceId}
        empty={state ? `No instance is ${state}.` : "No instances. Deploy one from an AVAILABLE package."}
        onRowClick={(i) => setSelected(i.instanceId)} selectedKey={selected} onRows={onRows}
        columns={[
          { header: "Instance", render: (i) => <Link to={`/rapps/${i.instanceId}`} onClick={(e) => e.stopPropagation()}><Id value={i.instanceId} /></Link> },
          { header: "Package", render: (i) => names.name(i.packageId) ?? <Id value={i.packageId} /> },
          { header: "State", render: (i) => <StateBadge state={i.state} /> },
          { header: "Lifecycle", render: (i) => <LifecycleCell state={i.state} instanceId={i.instanceId} /> },
          { header: "Autonomy", render: (i) => <StateBadge state={i.autonomyMode} /> },
          { header: "Headline KPI", render: (i) => <HeadlineKpi kpi={kpis.get(i.instanceId)} /> },
          { header: "", className: "actions", render: (i) => <InstanceActions inst={i} /> },
        ]} />
      <p className="gap-note">Actions in 24 h and refusals per rApp are not served by the backend yet, so they are not shown.</p>
      {selected && <InstanceDrawer id={selected} onClose={() => setSelected(null)} />}
    </Card>
  );
}
