/** The rApp detail "Faults" box: the faults the rApp reported, paged by rApp Management (`/rapp-mgmt/instances/{id}/faults`, SCALE.md P1).
 * Section id `rapp.faults`. */
import type { FaultReport } from "../../../api/types";
import { Card, SeverityChip } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { formatTime } from "../../../lib/domain";
import { faultsPath } from "../data/queries";

/** The box of instance `id`. */
export function Faults({ id }: { id: string }) {
  return (
    <Card section="rapp.faults" title="Faults">
      <ServerTable<FaultReport> path={faultsPath(id)} rowKey={(f) => f.faultId} empty="No faults reported." columns={[
        { header: "Severity", render: (f) => <SeverityChip severity={f.severity} /> },
        { header: "Description", render: (f) => f.description ?? "—" },
        { header: "Reported", render: (f) => formatTime(f.reportedAt) },
      ]} />
    </Card>
  );
}
