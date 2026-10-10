/** Section `safeguards.stopped`: everything stopped at RAN NF OAM, by invoker id, including rApps that are not instances of this platform; paged on
 * the server. Resume is per instance, in the limits table. */
import type { RappKill } from "../../../api/types";
import { Card, Id } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { formatTime } from "../../../lib/domain";
import { STOPPED } from "../data/queries";

/** The stopped list. */
export function Stopped() {
  return (
    <Card section="safeguards.stopped" title="Stopped rApps">
      <p className="muted small">Everything stopped at RAN NF OAM, by invoker id, including rApps that are not instances of this platform. Resume an instance from the table above.</p>
      <ServerTable<RappKill> path={STOPPED} rowKey={(r) => r.invokerId} empty="No rApp is stopped." columns={[
        { header: "Invoker", render: (r) => <Id value={r.invokerId} /> },
        { header: "Stopped by", render: (r) => r.killedBy },
        { header: "Reason", render: (r) => r.reason ?? <span className="muted">—</span> },
        { header: "Since", render: (r) => formatTime(r.killedAt) },
      ]} />
    </Card>
  );
}
