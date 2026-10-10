/** The lifecycle buttons of one rApp instance (call flow 07): mark bootstrapped, upgrade, resolve an upgrade, recover, terminate, delete. Shared
 * by the rApps tables, the pinned cards, the instance drawer and the rApp detail page (`pages/rapp-detail`), so an instance offers the same
 * actions everywhere. Each button is role-gated (`ActionButton`, `Can`). */
import type { InstanceSummary } from "../../../api/types";
import { ActionButton, Can } from "../../../components/ui";
import { instanceBase } from "../data/queries";

/** The buttons legal from the instance's state; `withUpgrade` opens the upgrade dialog (left out where no dialog is mounted). */
export function InstanceActions({ inst, withUpgrade }: { inst: Pick<InstanceSummary, "instanceId" | "state">; withUpgrade?: () => void }) {
  const base = instanceBase(inst.instanceId);
  return (
    <div className="row gap end">
      {inst.state === "DEPLOYING" && <ActionButton label="Mark bootstrapped" title="Simulates the rApp container finishing R1 bootstrap + SME/DME registration (DEMO_RUNBOOK step)" action={{ method: "POST", path: `${base}/bootstrap-complete`, success: "Instance RUNNING" }} />}
      {inst.state === "RUNNING" && withUpgrade && <Can method="POST" path={`${base}/upgrade`}><button type="button" className="btn" onClick={(e) => { e.stopPropagation(); withUpgrade(); }}>Upgrade…</button></Can>}
      {inst.state === "UPGRADING" && <>
        <ActionButton label="Upgrade succeeded" action={{ method: "POST", path: `${base}/upgrade/resolve`, query: { succeeded: true }, success: "Upgrade committed" }} />
        <ActionButton label="Upgrade failed" action={{ method: "POST", path: `${base}/upgrade/resolve`, query: { succeeded: false }, success: "Upgrade rolled back" }} />
      </>}
      {inst.state === "FAULTED" && <ActionButton label="Recover" action={{ method: "POST", path: `${base}/recover`, success: "Recovering — instance re-enters DEPLOYING" }} />}
      {["RUNNING", "FAULTED", "DEPLOYING"].includes(inst.state) && <ActionButton label="Terminate" tone="danger" confirm="Terminate this rApp instance? Its NFO workload is torn down, its usage registration stopped and its credentials revoked." action={{ method: "POST", path: `${base}/terminate`, success: "Instance UNDEPLOYED" }} />}
      {inst.state === "UNDEPLOYED" && <ActionButton label="Delete" tone="danger" confirm="Delete this instance record permanently?" action={{ method: "DELETE", path: base, success: "Instance deleted" }} />}
    </div>
  );
}
