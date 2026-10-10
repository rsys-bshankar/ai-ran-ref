/** The Rollouts tab (SCALE.md, rApps: "upgrade in progress" becomes a paged list): every instance UPGRADING right now, paged by rApp Management
 * (`GET /rapp-mgmt/instances?state=UPGRADING`), with the resolve actions. A row opens the instance drawer, which names the replacement instance.
 * Canary or wave progress per rollout is not served by the backend (a single replacement per upgrade). Section id `rapps.rollouts`. */
import { useState } from "react";
import { Link } from "react-router-dom";

import type { InstanceSummary } from "../../../api/types";
import { Card, Id } from "../../../components/ui";
import { formatCount } from "../../../kit/Kpi";
import { ServerTable } from "../../../kit/ServerTable";
import { count } from "../../../data/summary";
import { INSTANCES_PATH, usePackageNames, useRappsSummary } from "../data/queries";
import { InstanceActions } from "./InstanceActions";
import { InstanceDrawer } from "./InstanceDrawer";
import { LifecycleCell } from "./LifecycleCell";

/** The rollouts table. */
export function Rollouts() {
  const [selected, setSelected] = useState<string | null>(null);
  const names = usePackageNames();
  const summary = useRappsSummary();
  const n = count(summary.data, "instances.UPGRADING");
  return (
    <Card section="rapps.rollouts" title="Rollouts in progress" sub={n === null ? "instances being upgraded" : `${formatCount(n)} instance${n === 1 ? "" : "s"} being upgraded`}>
      <ServerTable<InstanceSummary> path={INSTANCES_PATH} query={{ state: "UPGRADING" }} rowKey={(i) => i.instanceId}
        empty="No upgrade in progress. Start one from a RUNNING instance (Upgrade…)."
        onRowClick={(i) => setSelected(i.instanceId)} selectedKey={selected}
        columns={[
          { header: "Instance", render: (i) => <Link to={`/rapps/${i.instanceId}`} onClick={(e) => e.stopPropagation()}><Id value={i.instanceId} /></Link> },
          { header: "From package", render: (i) => names.name(i.packageId) ?? <Id value={i.packageId} /> },
          { header: "Lifecycle", render: (i) => <LifecycleCell state={i.state} instanceId={i.instanceId} /> },
          { header: "", className: "actions", render: (i) => <InstanceActions inst={i} /> },
        ]} />
      <p className="gap-note">Canary and wave progress per rollout is not served by the backend: an upgrade is one replacement instance, resolved as a whole.</p>
      {selected && <InstanceDrawer id={selected} onClose={() => setSelected(null)} />}
    </Card>
  );
}
