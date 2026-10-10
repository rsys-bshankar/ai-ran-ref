/** Autonomy in the last 24 h (`dashboard.autonomy`, handoff `Main.dc.html`): how the rApps' actions were decided (counts by disposition from the
 * summary) and the latest six decision records, each linking to its record. "By rApp" from the mockup is not shown: it needs a count per rApp. */
import { Link } from "react-router-dom";

import type { DecisionRecord } from "../../../api/types";
import { Card, StateBadge } from "../../../components/ui";
import { count } from "../../../data/summary";
import { formatCount } from "../../../kit/Kpi";
import { QueryState } from "../../../kit/states";
import { formatTime } from "../../../lib/domain";
import { useDashboardSummary, useRecentDecisions } from "../data/queries";

/** The dispositions counted, with the word the tile shows. */
const DISPOSITIONS = [["DIRECT", "autonomous"], ["APPROVED", "approved"], ["REJECTED", "rejected"], ["EXPIRED", "expired"], ["REFUSED", "refused"]] as const;

/** The card. */
export function AutonomySummary() {
  const s = useDashboardSummary().data;
  const feed = useRecentDecisions();
  return (
    <Card section="dashboard.autonomy" title="Autonomy · 24 h" actions={<Link className="small" to="/decisions">All {formatCount(count(s, "decisions24h.total"))} →</Link>}>
      <div className="tiles">
        {DISPOSITIONS.map(([d, word]) => (
          <div key={d} className="tile"><b className="num">{formatCount(count(s, `decisions24h.${d}`))}</b><span className="small muted">{word}</span></div>
        ))}
      </div>
      <span className="eyebrow">Latest decisions</span>
      <QueryState q={feed} empty="No decisions recorded yet.">
        <ul className="list feed">
          {(feed.data ?? []).map((d: DecisionRecord) => (
            <li key={d.decisionId} className="small">
              <StateBadge state={d.disposition} />
              <span className="grow"><Link to={`/decisions/${d.decisionId}`}><strong>{d.invokerId}</strong></Link>
                <span className="muted"> · {d.changeCount} change{d.changeCount === 1 ? "" : "s"} on {d.managedElements.length} element{d.managedElements.length === 1 ? "" : "s"}</span></span>
              <span className="mono xs muted">{formatTime(d.occurredAt)}</span>
            </li>
          ))}
        </ul>
      </QueryState>
    </Card>
  );
}
