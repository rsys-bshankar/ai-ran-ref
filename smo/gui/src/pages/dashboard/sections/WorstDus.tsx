/** "Worst DUs" (`dashboard.worst`, handoff `Main.dc.html`). ⚠ Data gap (BRIEF §5, SCALE.md P4): no route ranks elements by their open alarms on the
 * server, and ranking them in the browser would need every alarm. So the box says so and links to the critical and major alarms instead. */
import { Link } from "react-router-dom";

import { Card } from "../../../components/ui";
import { count } from "../../../data/summary";
import { formatCount } from "../../../kit/Kpi";
import { useDashboardSummary } from "../data/queries";

/** The box: its gap note and the links that stand in for the ranking. */
export function WorstDus() {
  const s = useDashboardSummary().data;
  return (
    <Card section="dashboard.worst" title="Worst DUs" sub="needs a ranking on the server">
      <p className="kpi-v" aria-label="not available">—</p>
      <p className="gap-note">Not available yet: the backend has no ranking of elements by open alarms.</p>
      <ul className="list">
        <li><span className="sev sev-cr">critical</span><span className="grow">{formatCount(count(s, "alarms.critical"))} alarms</span><Link className="small" to="/alarms">Alarms →</Link></li>
        <li><span className="sev sev-mj">major</span><span className="grow">{formatCount(count(s, "alarms.major"))} alarms</span><Link className="small" to="/alarms">Alarms →</Link></li>
      </ul>
    </Card>
  );
}
