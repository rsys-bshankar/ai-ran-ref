/** Infrastructure → NF deployments: one deployment's row actions (heal, scale, terminate, asynchronous terminate) and its drawer (attributes, the
 * deployment-manager report buttons of OI-3-nfo-abnormal, LCM operations, linked O-Cloud resources). Moved unchanged from the pre-redesign page. */
import { useState } from "react";

import type { NfDeployment } from "../../../api/types";
import { ActionButton, Can, DataTable, Drawer, Field, Id, KeyValue, StateBadge } from "../../../components/ui";
import { useDeploymentDetail } from "../data/queries";

/** The lifecycle buttons a deployment's state allows. */
export function DeploymentActions({ d }: { d: NfDeployment }) {
  const base = `/nfo/deployments/${d.nfDeploymentId}`;
  return (
    <div className="row gap end">
      {(d.state === "RUNNING" || d.state === "ABNORMAL") && <ActionButton label="Heal" action={{ method: "POST", path: `${base}/heal`, success: "Heal requested" }} />}
      {d.state === "RUNNING" && <ActionButton label="Scale" action={{ method: "POST", path: `${base}/scale`, success: "Scale requested" }} />}
      {d.state !== "DELETING" && <ActionButton label="Terminate" tone="danger" confirm={`Terminate deployment ${d.name}?`} action={{ method: "DELETE", path: base, success: "Terminate requested" }} />}
      {!["TERMINATING", "DELETING"].includes(d.state) && <ActionButton label="Terminate (async)" tone="danger" title="Leaves the deployment TERMINATING until the deployment manager reports (OI-3-nfo-abnormal)" confirm={`Start an asynchronous uninstall of ${d.name}?`} action={{ method: "DELETE", path: base, query: { async_uninstall: true }, success: "Uninstall started — the deployment manager completes it" }} />}
    </div>
  );
}

// OI-3-nfo-abnormal: what the deployment manager (O2 DMS) would report — no real DMS runs here
const DMS_EVENTS: Record<string, string[]> = {
  TERMINATING: ["UNINSTALL_COMPLETE", "UNINSTALL_FAILED"], DELETING: ["DELETE_COMPLETE", "DELETE_FAILED"],
  INSTANTIATING: ["RUNTIME_FAILURE"], RUNNING: ["RUNTIME_FAILURE"], UPDATING: ["RUNTIME_FAILURE"],
};

/** The buttons that stand in for the deployment manager's report on this deployment's state. */
function DmsReport({ d }: { d: NfDeployment }) {
  const events = DMS_EVENTS[d.state] ?? [];
  const [detail, setDetail] = useState("");
  if (events.length === 0) return null;
  return (
    <>
      <h3>Deployment manager report</h3>
      <div className="form inline">
        <Field label="Detail" hint="why, for a failure"><input value={detail} onChange={(e) => setDetail(e.target.value)} /></Field>
        {events.map((event) => <ActionButton key={event} label={event} tone={event.endsWith("FAILED") || event === "RUNTIME_FAILURE" ? "danger" : undefined}
          action={{ method: "POST", path: `/nfo/deployments/${d.nfDeploymentId}/dms-notifications`, json: { event, detail: detail || null }, success: `${event} reported` }} />)}
      </div>
    </>
  );
}

/** The drawer of one deployment. */
export function DeploymentDrawer({ d, onClose }: { d: NfDeployment; onClose: () => void }) {
  const { resources, operations: ops } = useDeploymentDetail(d.nfDeploymentId);
  return (
    <Drawer title={d.name} onClose={onClose}>
      <div className="row between"><StateBadge state={d.state} /><DeploymentActions d={d} /></div>
      <KeyValue items={[
        ["Deployment ID", <code>{d.nfDeploymentId}</code>], ["Descriptor", <code>{d.nfDeploymentDescriptorId}</code>],
        ["Cluster (placement)", d.clusterId], ["Workload ref", d.workloadRef], ["Required resource type", d.requiredResourceTypeId],
        ["Abnormal because", d.abnormalReason && <span className="text-bad">{d.abnormalReason}</span>],
      ]} />
      <Can method="POST" path={`/nfo/deployments/${d.nfDeploymentId}/dms-notifications`}><DmsReport d={d} /></Can>
      <h3>LCM operations</h3>
      <DataTable rows={ops.data} error={ops.error} rowKey={(o) => o.operationId} empty="No operations recorded." columns={[
        { header: "Operation", render: (o) => <code>{o.operationType}</code> }, { header: "Status", render: (o) => <StateBadge state={o.status} /> },
        { header: "ID", render: (o) => <Id value={o.operationId} /> },
      ]} />
      <h3>O-Cloud resources</h3>
      <DataTable rows={resources.data} error={resources.error} rowKey={(r) => r.resourceLinkId} empty="No linked resources." columns={[
        { header: "Resource", render: (r) => <code>{r.resourceRef}</code> }, { header: "Type", render: (r) => r.vresourceType },
      ]} />
    </Drawer>
  );
}
