/** Section `approvals.impact`: what one request would touch, as tiles, from the fields the request carries (changes, managed elements, access
 * scope, what a lapse does). The predicted KPI effect of the mockup is not sent by the rApp: a gap note says so instead of a number. */
import type { ApprovalDetail } from "../../../api/types";
import { Kpi } from "../../../kit/Kpi";

/** The tiles. */
export function ImpactTiles({ approval: a }: { approval: ApprovalDetail }) {
  return (
    <div data-section="approvals.impact">
      <div className="tiles">
        <Kpi label="Changes" value={a.changeCount} foot="values it would write" />
        <Kpi label="Managed elements" value={a.managedElements.length} foot={a.managedElements.slice(0, 3).join(", ") || "—"} />
        <Kpi label="Access scope" value={a.accessScope ?? "—"} foot="the rApp's scope claim" />
        <Kpi label="If nobody decides" value={a.onTimeout === "REJECT" ? "rejected" : "expires"} foot="nothing is written either way" />
      </div>
      <p className="gap-note">Expected KPI impact is not shown: the rApp does not send a prediction with the request.</p>
    </div>
  );
}
