/** The upgrade dialog of one RUNNING instance: pick an AVAILABLE package, start the two-row upgrade (call flow 07). Opened from the instance
 * drawer here and from the rApp detail page. */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { InstanceSummary } from "../../../api/types";
import { Field, Modal } from "../../../components/ui";
import { instanceBase, useAvailablePackages } from "../data/queries";

/** The dialog; closes itself once the upgrade has started. */
export function UpgradeModal({ inst, onClose }: { inst: Pick<InstanceSummary, "instanceId" | "packageId">; onClose: () => void }) {
  const packages = useAvailablePackages();
  const [target, setTarget] = useState("");
  const action = useSmoAction();
  const candidates = (packages.data ?? []).filter((p) => p.packageId !== inst.packageId);
  return (
    <Modal title="Upgrade instance" onClose={onClose}>
      <form className="form" onSubmit={(e) => {
        e.preventDefault();
        action.mutate({ method: "POST", path: `${instanceBase(inst.instanceId)}/upgrade`, json: { newPackageId: target }, success: "Upgrade started — resolve it once the new instance bootstraps" }, { onSuccess: onClose });
      }}>
        <p className="muted small">Starts the two-row upgrade: the instance goes UPGRADING while a replacement deploys; resolving success commits it, failure rolls back (auto-rollback, Annex A.1.2.2.1).</p>
        <Field label="New package (AVAILABLE)">
          <select value={target} onChange={(e) => setTarget(e.target.value)} required>
            <option value="">Choose…</option>
            {candidates.map((p) => <option key={p.packageId} value={p.packageId}>{p.name} {p.version}</option>)}
          </select>
        </Field>
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn primary" disabled={!target || action.isPending}>Start upgrade</button></div>
      </form>
    </Modal>
  );
}
