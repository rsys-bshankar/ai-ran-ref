/** The version history of one rApp instance (OI-1-sa-rollback): committed upgrades and rollbacks, newest first, shown ten at a time with
 * "show more", and the Roll back action. Rollback is an upgrade back to the newest version not already rolled back; it is resolved like any
 * upgrade once the replacement bootstraps. Used by the instance drawer and by the rApp detail page's lifecycle history. */
import { useState } from "react";

import { ActionButton, DataTable, StateBadge } from "../../../components/ui";
import { formatTime } from "../../../lib/domain";
import { instanceBase, useVersions } from "../data/queries";

/** Rows shown before "show more". */
const STEP = 10;

/** The history table and the Roll back button (RUNNING instances with a rollback target only). */
export function VersionHistory({ id, state, title = "Version history" }: { id: string; state: string; title?: string }) {
  const base = instanceBase(id);
  const history = useVersions(id);
  const [shown, setShown] = useState(STEP);
  const target = history.data?.rollbackTarget;
  const versions = history.data?.versions;
  return (
    <>
      <div className="row between">
        <h3>{title}</h3>
        {state === "RUNNING" && target && <ActionButton label="Roll back" tone="danger"
          confirm={`Roll back to package ${target.previousPackageId.slice(0, 8)} and the configuration it ran?`}
          action={{ method: "POST", path: `${base}/rollback`, success: "Rollback started — resolve it once the replacement bootstraps" }} />}
      </div>
      <DataTable rows={versions?.slice(0, shown)} loading={history.isLoading} error={history.error} rowKey={(v) => v.versionId}
        empty="No upgrades committed — nothing to roll back to." columns={[
          { header: "Kind", render: (v) => <StateBadge state={v.kind} /> },
          { header: "From package", render: (v) => <code>{v.previousPackageId.slice(0, 8)}</code> },
          { header: "To package", render: (v) => <code>{v.packageId.slice(0, 8)}</code> },
          { header: "Rolled back", render: (v) => (v.kind === "UPGRADE" ? (v.rolledBackByVersionId ? "yes" : "no") : "—") },
          { header: "Committed", render: (v) => formatTime(v.committedAt) },
        ]} />
      {versions && versions.length > shown && (
        <div className="row between pager">
          <span className="muted small">{shown} of {versions.length} shown</span>
          <button type="button" className="btn small" onClick={() => setShown(shown + STEP)}>Show more</button>
        </div>
      )}
    </>
  );
}
