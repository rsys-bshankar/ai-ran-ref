/** Infrastructure → NF deployments (`infrastructure.deployments` and `infrastructure.descriptors`): state tiles from the BFF summary (true counts;
 * a tile filters the table), the server-paged deployment table with its state filter and row actions, the deployment drawer, and the paged
 * NF deployment descriptors. Deployments are created by rApp Management / SO SMOS; here they are healed, scaled or terminated. */
import { useState } from "react";

import type { NfDeployment, NfDescriptor } from "../../../api/types";
import { Card, Id, StateBadge } from "../../../components/ui";
import { count } from "../../../data/summary";
import { formatCount, Kpi } from "../../../kit/Kpi";
import { ServerTable } from "../../../kit/ServerTable";
import { DEPLOYMENT_STATES, PATHS, useInfraSummary } from "../data/queries";
import { DeploymentActions, DeploymentDrawer } from "./DeploymentDrawer";

const TILES = ["RUNNING", "INSTANTIATING", "UPDATING", "TERMINATING", "ABNORMAL"] as const;

/** The deployments box. */
export function NfDeployments() {
  const [state, setState] = useState("");
  const [selected, setSelected] = useState<NfDeployment | null>(null);
  const summary = useInfraSummary();
  return (
    <>
      <div className="grid g5" data-section="infrastructure.deployment-tiles">
        {TILES.map((s) => {
          const n = count(summary.data, `deployments.${s}`);
          return <Kpi key={s} label={s} value={formatCount(n)} active={state === s} tone={s === "ABNORMAL" && n ? "hot" : undefined}
            onClick={() => setState(state === s ? "" : s)} title={`Show only ${s} deployments`} foot={`of ${formatCount(count(summary.data, "deployments.total"))}`} />;
        })}
      </div>
      <Card section="infrastructure.deployments" title="NF deployments" sub="Created by rApp Management / SO SMOS; the GUI heals, scales or terminates them."
        actions={<select value={state} onChange={(e) => setState(e.target.value)} aria-label="State"><option value="">All states</option>{DEPLOYMENT_STATES.map((s) => <option key={s}>{s}</option>)}</select>}>
        <ServerTable<NfDeployment> path={PATHS.deployments} query={{ state: state || undefined }} rowKey={(d) => d.nfDeploymentId} empty="No NF deployments."
          onRowClick={setSelected} selectedKey={selected?.nfDeploymentId} columns={[
            { header: "Name", render: (d) => <strong>{d.name}</strong> },
            { header: "Deployment", render: (d) => <Id value={d.nfDeploymentId} /> },
            { header: "State", render: (d) => <StateBadge state={d.state} /> },
            { header: "Cluster", render: (d) => d.clusterId },
            { header: "Resource type", render: (d) => d.requiredResourceTypeId ?? <span className="muted">—</span> },
            { header: "", className: "actions", render: (d) => <DeploymentActions d={d} /> },
          ]} />
      </Card>
      {selected && <DeploymentDrawer d={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

/** The descriptors box. */
export function NfDescriptors() {
  return (
    <Card section="infrastructure.descriptors" title="NF deployment descriptors" sub="Created by Onboarding's validation pipeline from each package's TOSCA definitions">
      <ServerTable<NfDescriptor> path={PATHS.descriptors} rowKey={(d) => d.nfDeploymentDescriptorId} empty="No descriptors." columns={[
        { header: "Descriptor", render: (d) => <Id value={d.nfDeploymentDescriptorId} /> }, { header: "Name", render: (d) => <code>{d.name}</code> },
        { header: "Package", render: (d) => <Id value={d.packageId} /> }, { header: "Resource type", render: (d) => d.requiredResourceTypeId ?? "—" },
      ]} />
    </Card>
  );
}
