/** The rApps page's main table (SCALE.md P1): every rApp instance, paged by rApp Management (`GET /rapp-mgmt/instances?state=`), with a flow 07
 * lifecycle column, the package name, autonomy mode and the lifecycle actions. A row opens the instance drawer. Section id `rapps.instances`. */
import { useState } from "react";
import { Link } from "react-router-dom";

import type { InstanceSummary } from "../../../api/types";
import { Card, Id, StateBadge } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { INSTANCE_STATES, INSTANCES_PATH, usePackageNames } from "../data/queries";
import { InstanceActions } from "./InstanceActions";
import { InstanceDrawer } from "./InstanceDrawer";
import { LifecycleCell } from "./LifecycleCell";

/** The instance table with its state filter. */
export function InstanceTable() {
  const [state, setState] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const names = usePackageNames();
  return (
    <Card section="rapps.instances" title="rApp instances" sub="paged by rApp Management · click a row for details"
      actions={
        <select value={state} onChange={(e) => setState(e.target.value)} aria-label="Filter by state">
          <option value="">All states</option>
          {INSTANCE_STATES.map((s) => <option key={s}>{s}</option>)}
        </select>}>
      <ServerTable<InstanceSummary> path={INSTANCES_PATH} query={{ state: state || undefined }} rowKey={(i) => i.instanceId}
        empty={state ? `No instance is ${state}.` : "No instances. Deploy one from an AVAILABLE package."}
        onRowClick={(i) => setSelected(i.instanceId)} selectedKey={selected}
        columns={[
          { header: "Instance", render: (i) => <Link to={`/rapps/${i.instanceId}`} onClick={(e) => e.stopPropagation()}><Id value={i.instanceId} /></Link> },
          { header: "Package", render: (i) => names.name(i.packageId) ?? <Id value={i.packageId} /> },
          { header: "State", render: (i) => <StateBadge state={i.state} /> },
          { header: "Lifecycle", render: (i) => <LifecycleCell state={i.state} instanceId={i.instanceId} /> },
          { header: "Autonomy", render: (i) => <StateBadge state={i.autonomyMode} /> },
          { header: "", className: "actions", render: (i) => <InstanceActions inst={i} /> },
        ]} />
      <p className="gap-note">Health, actions in 24 h and refusals per rApp are not served by the backend yet, so they are not shown.</p>
      {selected && <InstanceDrawer id={selected} onClose={() => setSelected(null)} />}
    </Card>
  );
}
